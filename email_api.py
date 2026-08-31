"""
Email for Muno.

Sending goes over SMTP and reading over IMAP, which between them cover nearly
every consumer mail provider: Gmail, Outlook, Yahoo, iCloud, Zoho, Fastmail,
GMX, AOL, and any host that exposes standard mail ports. The provider is
detected from the address, so the user only supplies an address and a password.

App passwords
-------------
Most providers no longer accept an account password from third-party apps once
two-factor authentication is on. They issue a separate app password instead:

    Gmail    https://myaccount.google.com/apppasswords
    Yahoo    https://login.yahoo.com/account/security
    iCloud   https://appleid.apple.com  (Sign-In and Security, App-Specific Passwords)
    Outlook  https://account.microsoft.com/security

Microsoft 365 accounts managed by an employer often disable basic SMTP
altogether, in which case sending needs OAuth2 and this module will report the
rejection rather than silently failing.

Attachments are resolved through Muno's local file index, so "email that PDF to
Sarah" works without anyone typing a path.
"""
import imaplib
import mimetypes
import os
import re
import smtplib
import ssl
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr

import file_index

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 24 * 1024 * 1024

# host, smtp port, imap host, imap port
PROVIDERS = {
    "gmail.com": ("smtp.gmail.com", 587, "imap.gmail.com", 993),
    "googlemail.com": ("smtp.gmail.com", 587, "imap.gmail.com", 993),
    "outlook.com": ("smtp-mail.outlook.com", 587, "outlook.office365.com", 993),
    "hotmail.com": ("smtp-mail.outlook.com", 587, "outlook.office365.com", 993),
    "live.com": ("smtp-mail.outlook.com", 587, "outlook.office365.com", 993),
    "msn.com": ("smtp-mail.outlook.com", 587, "outlook.office365.com", 993),
    "office365.com": ("smtp.office365.com", 587, "outlook.office365.com", 993),
    "yahoo.com": ("smtp.mail.yahoo.com", 587, "imap.mail.yahoo.com", 993),
    "yahoo.co.uk": ("smtp.mail.yahoo.com", 587, "imap.mail.yahoo.com", 993),
    "ymail.com": ("smtp.mail.yahoo.com", 587, "imap.mail.yahoo.com", 993),
    "aol.com": ("smtp.aol.com", 587, "imap.aol.com", 993),
    "icloud.com": ("smtp.mail.me.com", 587, "imap.mail.me.com", 993),
    "me.com": ("smtp.mail.me.com", 587, "imap.mail.me.com", 993),
    "mac.com": ("smtp.mail.me.com", 587, "imap.mail.me.com", 993),
    "zoho.com": ("smtp.zoho.com", 587, "imap.zoho.com", 993),
    "fastmail.com": ("smtp.fastmail.com", 587, "imap.fastmail.com", 993),
    "gmx.com": ("mail.gmx.com", 587, "imap.gmx.com", 993),
    "gmx.net": ("mail.gmx.net", 587, "imap.gmx.net", 993),
    "mail.com": ("smtp.mail.com", 587, "imap.mail.com", 993),
    "yandex.com": ("smtp.yandex.com", 587, "imap.yandex.com", 993),
    "protonmail.com": ("127.0.0.1", 1025, "127.0.0.1", 1143),  # Proton Bridge
    "proton.me": ("127.0.0.1", 1025, "127.0.0.1", 1143),
}


# Where Muno remembers the sending identity when no environment variable is set.
# This is a preference, not a secret, so it lives alongside the other settings.
IDENTITY_FILE = os.path.join(os.path.expanduser("~/.codex_pet"), "email.json")


def _stored_identity():
    """Return the saved sending address, if any."""
    try:
        import json

        with open(IDENTITY_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return str(data.get("address", "")).strip() if isinstance(data, dict) else ""
    except (OSError, ValueError):
        return ""


def set_identity(address):
    """Remember which address Muno sends from. Stores no password."""
    import json

    clean = str(address or "").strip()
    if clean and not _EMAIL_PATTERN.match(clean):
        return f"❌ '{clean}' doesn't look like an email address."
    os.makedirs(os.path.dirname(IDENTITY_FILE), exist_ok=True)
    temporary = f"{IDENTITY_FILE}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump({"address": clean}, handle, indent=2)
    os.replace(temporary, IDENTITY_FILE)
    return f"✅ I'll send email as {clean}."


def _address():
    return (os.getenv("EMAIL_ADDRESS") or "").strip() or _stored_identity()


def _password():
    return (os.getenv("EMAIL_PASSWORD") or "").strip()


def is_configured():
    """True when any backend can send mail."""
    return bool(available_backends())


# ---------------------------------------------------------------------------
# Backend discovery
#
# The point of this layer is to avoid asking for a password per service. Muno
# prefers whatever is already signed in on this machine, and only falls back to
# an explicit password when nothing else can do the job.
#
#   outlook  a configured desktop mail account, driven through COM. No password.
#   gmail    an existing browser session for a Google account. No password.
#   smtp     an explicit address and app password.
#   mailto   hands the drafted message to the default mail client to send.
# ---------------------------------------------------------------------------

BACKEND_OUTLOOK = "outlook"
BACKEND_GMAIL = "gmail"
BACKEND_SMTP = "smtp"
BACKEND_MAILTO = "mailto"

# The desktop mail client is opt-in. It can send without any password, but it
# sends from whichever account it is configured with, which may not be the
# address the user wants to be seen as. Enable it with MUNO_EMAIL_USE_OUTLOOK=1
# or by asking for the outlook backend explicitly.
def outlook_enabled():
    return (os.getenv("MUNO_EMAIL_USE_OUTLOOK") or "0").strip().lower() in {"1", "true", "yes"}


def _com():
    try:
        import win32com.client as client

        return client
    except ImportError:
        return None


def outlook_accounts():
    """Return the SMTP addresses configured in the desktop mail client."""
    client = _com()
    if client is None:
        return []
    try:
        import pythoncom

        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        namespace = client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        return [
            str(namespace.Accounts.Item(i).SmtpAddress)
            for i in range(1, namespace.Accounts.Count + 1)
        ]
    except Exception:
        return []


def outlook_available():
    return bool(outlook_accounts())


def gmail_available():
    """True when the sending identity is a Google address we can drive in a browser."""
    address = _address().lower()
    if not address:
        return False
    if not address.endswith(("@gmail.com", "@googlemail.com")):
        return False
    try:
        import browser as browser_control

        return browser_control is not None
    except Exception:
        return False


def smtp_available():
    return bool(_address() and _password())


def available_backends():
    """Backends that could send right now, preferred first."""
    backends = []
    if gmail_available():
        backends.append(BACKEND_GMAIL)
    if smtp_available():
        backends.append(BACKEND_SMTP)
    if outlook_available() and outlook_enabled():
        backends.append(BACKEND_OUTLOOK)
    backends.append(BACKEND_MAILTO)
    return backends


def _choose_backend(needs_attachments=False):
    """Pick a backend, honouring an explicit preference when it is usable.

    Preference order keeps the user's chosen sending identity intact. Switching
    to a different account just because it happens to be configured locally
    would quietly change who the recipient sees the mail from.
    """
    preference = (os.getenv("MUNO_EMAIL_BACKEND") or "auto").strip().lower()

    # An explicit request for the desktop client overrides the opt-in default.
    if preference == BACKEND_OUTLOOK and outlook_available():
        return BACKEND_OUTLOOK

    usable = available_backends()
    if preference in usable:
        # mailto cannot carry attachments, so never silently drop them.
        if not (needs_attachments and preference == BACKEND_MAILTO):
            return preference

    # Send from the account that actually owns the address, when enabled.
    address = _address().lower()
    if outlook_enabled() and address and address in [a.lower() for a in outlook_accounts()]:
        return BACKEND_OUTLOOK

    for candidate in (BACKEND_GMAIL, BACKEND_SMTP, BACKEND_OUTLOOK, BACKEND_MAILTO):
        if candidate in usable:
            if needs_attachments and candidate == BACKEND_MAILTO:
                continue
            return candidate
    return BACKEND_MAILTO


def _provider_settings():
    """Resolve SMTP/IMAP settings from the address, allowing manual overrides."""
    address = _address()
    domain = address.split("@")[-1].lower() if "@" in address else ""
    smtp_host, smtp_port, imap_host, imap_port = PROVIDERS.get(
        domain, (f"smtp.{domain}", 587, f"imap.{domain}", 993)
    )

    smtp_host = (os.getenv("EMAIL_SMTP_HOST") or smtp_host).strip()
    imap_host = (os.getenv("EMAIL_IMAP_HOST") or imap_host).strip()
    try:
        smtp_port = int(os.getenv("EMAIL_SMTP_PORT") or smtp_port)
    except ValueError:
        smtp_port = 587
    try:
        imap_port = int(os.getenv("EMAIL_IMAP_PORT") or imap_port)
    except ValueError:
        imap_port = 993
    return smtp_host, smtp_port, imap_host, imap_port


def _friendly_smtp_error(exc):
    """Translate SMTP failures into something the user can act on."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        address = _address()
        provider = address.split("@")[-1].lower() if "@" in address else "your provider"
        return (
            "The mail server rejected the sign-in. Most providers need an app "
            f"password rather than your normal password. Create one for {provider} "
            "and set it as EMAIL_PASSWORD."
        )
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "The mail server refused the recipient address. Check it for typos."
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "The mail server refused the sending address. Check EMAIL_ADDRESS."
    if isinstance(exc, smtplib.SMTPConnectError):
        return "I couldn't connect to the mail server. Check your connection."
    if isinstance(exc, (TimeoutError, OSError)) and "timed out" in str(exc).lower():
        return "The mail server took too long to respond. Try again."
    if isinstance(exc, ssl.SSLError):
        return "The secure connection to the mail server failed."
    return "The mail server reported a problem while sending."


# ---------------------------------------------------------------------------
# Recipient resolution
# ---------------------------------------------------------------------------

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def _looks_like_email(value):
    return bool(_EMAIL_PATTERN.match(str(value or "").strip()))


def resolve_recipients(recipients):
    """Turn names or addresses into a list of addresses.

    Names are looked up in Muno's contacts. Returns (addresses, unresolved).
    """
    if isinstance(recipients, str):
        parts = [p for p in re.split(r"[,;]+", recipients) if p.strip()]
    else:
        parts = list(recipients or [])

    import tools  # Imported lazily to avoid a circular import at module load.

    addresses = []
    unresolved = []
    for part in parts:
        candidate = str(part).strip().strip("<>")
        if _looks_like_email(candidate):
            addresses.append(candidate)
            continue
        # Fall back to the shared contact book.
        contact = tools.lookup_contact_email(candidate)
        if contact:
            addresses.append(contact)
        else:
            unresolved.append(candidate)
    return addresses, unresolved


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def _attach_files(message, attachments):
    """Attach local files, resolving them by name. Returns (attached, problems)."""
    if not attachments:
        return [], []
    if isinstance(attachments, str):
        candidates = [a for a in re.split(r"[,;]+", attachments) if a.strip()]
    else:
        candidates = list(attachments)

    attached = []
    problems = []
    total = 0
    for candidate in candidates:
        reference = str(candidate).strip()
        if not reference:
            continue
        path, _alternatives = file_index.resolve_file(reference)
        if not path:
            problems.append(f"I couldn't find a file called '{reference}'")
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            problems.append(f"I couldn't read '{reference}'")
            continue
        if size > MAX_ATTACHMENT_BYTES:
            problems.append(
                f"'{os.path.basename(path)}' is {size / 1048576:.1f} MB, too large to email"
            )
            continue
        total += size
        if total > MAX_TOTAL_ATTACHMENT_BYTES:
            problems.append("the attachments together exceed the size most providers allow")
            break

        guessed, _encoding = mimetypes.guess_type(path)
        main_type, _, sub_type = (guessed or "application/octet-stream").partition("/")
        try:
            with open(path, "rb") as handle:
                message.add_attachment(
                    handle.read(),
                    maintype=main_type,
                    subtype=sub_type or "octet-stream",
                    filename=os.path.basename(path),
                )
            attached.append(os.path.basename(path))
        except OSError:
            problems.append(f"I couldn't read '{reference}'")
    return attached, problems


def _resolve_attachment_paths(attachments):
    """Turn attachment references into absolute paths. Returns (paths, problems)."""
    if not attachments:
        return [], []
    if isinstance(attachments, str):
        candidates = [a for a in re.split(r"[,;]+", attachments) if a.strip()]
    else:
        candidates = list(attachments)

    paths = []
    problems = []
    total = 0
    for candidate in candidates:
        reference = str(candidate).strip()
        if not reference:
            continue
        path, _alternatives = file_index.resolve_file(reference)
        if not path:
            problems.append(f"I couldn't find a file called '{reference}'")
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            problems.append(f"I couldn't read '{reference}'")
            continue
        if size > MAX_ATTACHMENT_BYTES:
            problems.append(
                f"'{os.path.basename(path)}' is {size / 1048576:.1f} MB, too large to email"
            )
            continue
        total += size
        if total > MAX_TOTAL_ATTACHMENT_BYTES:
            problems.append("the attachments together exceed the size most providers allow")
            break
        paths.append(path)
    return paths, problems


def _send_via_outlook(addresses, subject, body, attachment_paths, cc_addresses):
    """Send through the configured desktop mail account. No password needed."""
    client = _com()
    if client is None:
        raise RuntimeError("The desktop mail client isn't reachable.")
    try:
        import pythoncom

        pythoncom.CoInitialize()
    except Exception:
        pass

    outlook = client.Dispatch("Outlook.Application")
    mail = outlook.CreateItem(0)  # olMailItem
    mail.To = "; ".join(addresses)
    if cc_addresses:
        mail.CC = "; ".join(cc_addresses)
    mail.Subject = subject
    mail.Body = body
    for path in attachment_paths:
        mail.Attachments.Add(os.path.abspath(path))
    mail.Send()

    accounts = outlook_accounts()
    return accounts[0] if accounts else "your mail account"


def _send_via_gmail_web(addresses, subject, body, cc_addresses, attachment_paths=None):
    """Send from Gmail using the browser session that is already signed in.

    Attachments are pushed straight into Gmail's file input, which keeps the
    user's own address as the sender rather than handing off to another account.
    """
    import browser as browser_control

    query = {"view": "cm", "fs": "1", "to": ",".join(addresses), "su": subject, "body": body}
    if cc_addresses:
        query["cc"] = ",".join(cc_addresses)
    url = "https://mail.google.com/mail/?" + urllib.parse.urlencode(query)

    with browser_control._session() as page:
        page = browser_control._goto(page, url)
        page.bring_to_front()

        # The compose form only renders once signed in.
        composed = False
        for selector in ("div[aria-label='Message Body']", "textarea[name='body']",
                         "div[g_editable='true']"):
            try:
                page.wait_for_selector(selector, timeout=15000)
                composed = True
                break
            except Exception:
                continue
        if not composed:
            raise RuntimeError(
                "Gmail isn't signed in in my browser yet. Sign in once in that window "
                "(or point me at your normal Chrome profile), then ask me again."
            )

        if attachment_paths:
            try:
                file_input = page.locator("input[type='file']").first
                file_input.set_input_files([os.path.abspath(p) for p in attachment_paths],
                                           timeout=20000)
                # Wait for Gmail to finish uploading before sending.
                for _ in range(60):
                    page.wait_for_timeout(500)
                    text = page.inner_text("body")
                    if "Uploading" not in text:
                        break
            except Exception as exc:
                raise RuntimeError(
                    "I opened the draft but couldn't attach the file in Gmail "
                    f"({str(exc)[:80]}). It's ready for you to attach and send."
                )

        # Ctrl+Enter is Gmail's send shortcut and survives layout changes better
        # than hunting for the Send button.
        page.keyboard.press("Control+Enter")
        page.wait_for_timeout(2500)
    return _address()


def _send_via_mailto(addresses, subject, body, cc_addresses):
    """Hand the drafted message to the default mail client."""
    query = {"subject": subject, "body": body}
    if cc_addresses:
        query["cc"] = ",".join(cc_addresses)
    uri = "mailto:" + ",".join(addresses) + "?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
    os.startfile(uri)
    return None


def send_email(to, subject, body, attachments=None, cc=None):
    """
    Sends an email, optionally with files attached.

    Args:
        to: Recipient address, contact name, or several separated by commas.
        subject: The subject line.
        body: The message text.
        attachments: Optional file names or paths to attach.
        cc: Optional addresses to copy.

    Returns:
        Success/failure message
    """
    addresses, unresolved = resolve_recipients(to)
    if unresolved and not addresses:
        return (
            f"I don't have an email address for {', '.join(unresolved)}. "
            "Tell me the address, or save it first."
        )
    if not addresses:
        return "❌ Who should I send it to?"

    cc_addresses, _cc_unresolved = resolve_recipients(cc) if cc else ([], [])
    subject_line = str(subject or "").strip() or "(no subject)"
    text_body = str(body or "")

    attachment_paths, problems = _resolve_attachment_paths(attachments)
    backend = _choose_backend(needs_attachments=bool(attachment_paths))

    sent_as = None
    try:
        if backend == BACKEND_OUTLOOK:
            sent_as = _send_via_outlook(
                addresses, subject_line, text_body, attachment_paths, cc_addresses
            )
        elif backend == BACKEND_GMAIL:
            sent_as = _send_via_gmail_web(
                addresses, subject_line, text_body, cc_addresses, attachment_paths
            )
        elif backend == BACKEND_SMTP:
            message = EmailMessage()
            message["From"] = _address()
            message["To"] = ", ".join(addresses)
            if cc_addresses:
                message["Cc"] = ", ".join(cc_addresses)
            message["Subject"] = subject_line
            message.set_content(text_body)
            for path in attachment_paths:
                guessed, _encoding = mimetypes.guess_type(path)
                main_type, _, sub_type = (guessed or "application/octet-stream").partition("/")
                with open(path, "rb") as handle:
                    message.add_attachment(
                        handle.read(),
                        maintype=main_type,
                        subtype=sub_type or "octet-stream",
                        filename=os.path.basename(path),
                    )
            _send_via_smtp(message)
            sent_as = _address()
        else:
            _send_via_mailto(addresses, subject_line, text_body, cc_addresses)
    except Exception as exc:
        if backend == BACKEND_SMTP:
            return f"❌ {_friendly_smtp_error(exc)}"
        return f"❌ I couldn't send that: {str(exc)[:200]}"

    names = ", ".join(addresses)
    if backend == BACKEND_MAILTO:
        summary = (
            f"✅ I've drafted '{subject_line}' to {names} in your mail app. "
            "Press send there to deliver it"
        )
        if attachment_paths:
            summary += ", and attach the files yourself since a draft link can't carry them"
    else:
        summary = f"✅ Sent '{subject_line}' to {names}"
        if sent_as:
            summary += f" from {sent_as}"
        if attachment_paths:
            summary += f" with {', '.join(os.path.basename(p) for p in attachment_paths)} attached"

    if unresolved:
        summary += f". I skipped {', '.join(unresolved)} because I have no address for them"
    if problems:
        summary += ". Note: " + "; ".join(problems)
    return summary + "."


def _send_via_smtp(message):
    """Deliver a built message over SMTP with an explicit password."""
    smtp_host, smtp_port, _imap_host, _imap_port = _provider_settings()
    context = ssl.create_default_context()
    if smtp_port == 465:
        server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30, context=context)
    else:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
    with server:
        server.ehlo()
        if smtp_port != 465:
            try:
                server.starttls(context=context)
                server.ehlo()
            except smtplib.SMTPNotSupportedError:
                pass  # A local bridge may not offer TLS.
        server.login(_address(), _password())
        server.send_message(message)


def email_status():
    """Report how Muno will send mail, and what it is using to do it."""
    backends = available_backends()
    chosen = _choose_backend()
    address = _address()

    explanations = {
        BACKEND_OUTLOOK: "your desktop mail account (no password needed)",
        BACKEND_GMAIL: "your Gmail session in Muno's browser (no password needed)",
        BACKEND_SMTP: "a direct connection with your app password",
        BACKEND_MAILTO: "your default mail app, where you press send",
    }
    lines = [f"📧 I'll send using {explanations.get(chosen, chosen)}."]
    if address:
        lines.append(f"Sending as: {address}")
    other = [b for b in backends if b != chosen]
    if other:
        lines.append("Also available: " + ", ".join(other))
    if chosen == BACKEND_GMAIL:
        lines.append(
            "Sign in to Gmail once in my browser window if you haven't yet, and it "
            "stays signed in after that."
        )
    if chosen == BACKEND_MAILTO:
        lines.append(
            "For fully automatic sending, sign in to Gmail once in my browser, or set "
            "EMAIL_PASSWORD to an app password."
        )
    accounts = outlook_accounts()
    if accounts and not outlook_enabled():
        lines.append(
            f"A desktop mail account ({', '.join(accounts)}) is also on this machine "
            "but switched off, so I won't send from it. Set MUNO_EMAIL_USE_OUTLOOK=1 "
            "when you want to use it."
        )
    return "\n".join(lines)


def _read_via_outlook(limit, unread_only):
    """Read the desktop client's inbox. No password needed."""
    client = _com()
    if client is None:
        raise RuntimeError("The desktop mail client isn't reachable.")
    try:
        import pythoncom

        pythoncom.CoInitialize()
    except Exception:
        pass

    namespace = client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    inbox = namespace.GetDefaultFolder(6)  # olFolderInbox
    items = inbox.Items
    items.Sort("[ReceivedTime]", True)
    if unread_only:
        items = items.Restrict("[UnRead] = True")

    lines = []
    for index in range(1, min(items.Count, limit) + 1):
        try:
            item = items.Item(index)
            sender = str(getattr(item, "SenderName", "") or "unknown sender")
            subject = str(getattr(item, "Subject", "") or "(no subject)")
            lines.append(f"- {sender}: {subject[:80]}")
        except Exception:
            continue
    return lines


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _decode_header_value(raw):
    from email.header import decode_header, make_header

    try:
        return str(make_header(decode_header(raw or "")))
    except Exception:
        return str(raw or "")


def check_email(limit=5, unread_only=True, folder="INBOX"):
    """
    Lists recent messages in the mailbox.

    Args:
        limit: How many messages to list.
        unread_only: Only show unread messages when True.
        folder: Mailbox folder, defaults to INBOX.

    Returns:
        A readable summary of recent mail
    """
    try:
        count = max(1, min(int(limit or 5), 20))
    except (TypeError, ValueError):
        count = 5
    only_unread = unread_only if isinstance(unread_only, bool) else str(unread_only).lower() != "false"

    # The desktop client is only used for reading when it is explicitly enabled,
    # since its inbox belongs to a different account than the sending identity.
    if outlook_available() and outlook_enabled() and \
            (os.getenv("MUNO_EMAIL_BACKEND") or "auto").lower() != "smtp":
        try:
            lines = _read_via_outlook(count, only_unread)
            if not lines:
                return "📬 No unread messages." if only_unread else "📬 Nothing to show."
            label = "Unread" if only_unread else "Recent"
            return f"📬 {label} mail ({len(lines)}):\n" + "\n".join(lines)
        except Exception:
            pass  # Fall through to IMAP.

    if not smtp_available():
        if gmail_available():
            return (
                "I can't read your Gmail yet. I can open your inbox in the browser, "
                "or set EMAIL_PASSWORD to a Gmail app password and I'll read it here."
            )
        return (
            "I don't have a way to read your mail yet. Set EMAIL_PASSWORD to an app "
            "password, or enable the desktop mail client with MUNO_EMAIL_USE_OUTLOOK=1."
        )

    _smtp_host, _smtp_port, imap_host, imap_port = _provider_settings()

    try:
        connection = imaplib.IMAP4_SSL(imap_host, imap_port, timeout=30)
    except Exception:
        return "❌ I couldn't reach the mail server to read your inbox."

    try:
        try:
            connection.login(_address(), _password())
        except imaplib.IMAP4.error:
            return (
                "The mail server rejected the sign-in for reading. Most providers "
                "need an app password, and some require IMAP to be switched on."
            )

        status, _data = connection.select(str(folder or "INBOX"), readonly=True)
        if status != "OK":
            return f"❌ I couldn't open the '{folder}' folder."

        status, data = connection.search(None, "UNSEEN" if only_unread else "ALL")
        if status != "OK":
            return "❌ I couldn't search your mailbox."

        ids = (data[0] or b"").split()
        if not ids:
            return "📬 No unread messages." if only_unread else "📬 That folder is empty."

        chosen = ids[-count:][::-1]
        lines = []
        for message_id in chosen:
            status, payload = connection.fetch(
                message_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
            )
            if status != "OK" or not payload or not payload[0]:
                continue
            raw = payload[0][1]
            headers = BytesParser(policy=policy.default).parsebytes(raw)
            sender = _decode_header_value(headers.get("From", ""))
            name, address = parseaddr(sender)
            who = name or address or "unknown sender"
            subject = _decode_header_value(headers.get("Subject", "")) or "(no subject)"
            lines.append(f"- {who}: {subject[:80]}")

        label = "Unread" if only_unread else "Recent"
        return f"📬 {label} mail ({len(lines)}):\n" + "\n".join(lines)
    except Exception:
        return "❌ Something went wrong while reading your inbox."
    finally:
        try:
            connection.logout()
        except Exception:
            pass
