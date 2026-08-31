"""
Real-time microphone streaming for Muno.

The point of streaming rather than recording
--------------------------------------------
Recording a whole utterance and only then reacting makes the companion feel
deaf: nothing happens while you talk, then everything happens at once. Here the
microphone is consumed in 20 ms frames, so three things can happen immediately:

* a level is published about fifty times a second, so the interface can react
  while you are still speaking,
* the start of speech is detected within a frame or two, so Muno can stop
  talking and start listening the moment you interrupt it,
* the utterance is assembled as it arrives, so transcription starts the instant
  you stop rather than after a fixed delay.

Two details that matter more than they look
-------------------------------------------
**Pre-roll.** A detector only knows speech started *after* the level rises,
which is already a few frames into the first word. Keeping a short rolling buffer
of the frames just before that point and prepending them means the transcript
does not lose the beginning of words. Without this, "open Chrome" arrives as
"pen Chrome".

**An adaptive floor.** A fixed threshold works in one room and fails in the next.
The floor is estimated continuously from recent quiet frames, and the trigger
sits above it, with hysteresis so a wavering voice does not chop the utterance
into pieces.

This module deliberately has no Qt or network dependency, so it can be exercised
straight from a terminal:

    python voice_stream.py --monitor        live meter and detected speech
    python voice_stream.py --calibrate     measure this room's noise floor
"""
import argparse
import collections
import io
import math
import sys
import time
import wave

import numpy as np

SAMPLE_RATE = 16000
# 20 ms frames: fine enough for a responsive meter, coarse enough to be cheap.
FRAME_SAMPLES = 320
FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE

# Keep this much audio from before speech was detected, so word onsets survive.
PRE_ROLL_SECONDS = 0.32
# Silence this long ends an utterance.
END_SILENCE_SECONDS = 0.75
# Guard rails on utterance length.
MIN_SPEECH_SECONDS = 0.25
MAX_SPEECH_SECONDS = 20.0

# Window used to estimate the room's noise floor.
FLOOR_WINDOW_SECONDS = 3.0
# Speech must exceed the floor by this factor to count as a start.
START_MULTIPLIER = 2.8
# It only has to stay above this fraction of the start level to keep going,
# which stops a wavering voice from splitting one sentence into several.
CONTINUE_RATIO = 0.55
# Absolute floor so a silent room does not make the detector hair-trigger.
MIN_TRIGGER_LEVEL = 180.0


def frame_level(frame):
    """Root mean square amplitude of one int16 frame."""
    if frame.size == 0:
        return 0.0
    samples = frame.astype(np.float32)
    return float(np.sqrt(np.mean(samples * samples)))


def level_to_meter(level, trigger):
    """Map a level to 0.0-1.0 for display, scaled around the trigger point."""
    if trigger <= 0:
        trigger = MIN_TRIGGER_LEVEL
    # Log scaling matches how loudness is perceived far better than linear.
        # A level at the trigger reads about a third of full scale.
    ratio = max(level, 1.0) / max(trigger, 1.0)
    scaled = math.log10(1.0 + 9.0 * min(ratio / 3.0, 1.0))
    return max(0.0, min(1.0, scaled))


def wav_bytes(frames, sample_rate=SAMPLE_RATE):
    """Package raw int16 frames as a WAV file in memory."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"".join(frames))
    return buffer.getvalue()


class VoiceActivityDetector:
    """Turns a stream of frames into speech start, continue, and end events.

    Feed it one frame at a time. Each call returns a small dictionary describing
    what just happened, so the caller can drive a meter, an animation, and a
    transcription queue from a single source.
    """

    def __init__(self,
                 pre_roll_seconds=PRE_ROLL_SECONDS,
                 end_silence_seconds=END_SILENCE_SECONDS,
                 min_speech_seconds=MIN_SPEECH_SECONDS,
                 max_speech_seconds=MAX_SPEECH_SECONDS):
        self.pre_roll_frames = max(1, int(pre_roll_seconds / FRAME_SECONDS))
        self.end_silence_frames = max(1, int(end_silence_seconds / FRAME_SECONDS))
        self.min_speech_frames = max(1, int(min_speech_seconds / FRAME_SECONDS))
        self.max_speech_frames = max(1, int(max_speech_seconds / FRAME_SECONDS))

        self._floor_window = collections.deque(
            maxlen=max(8, int(FLOOR_WINDOW_SECONDS / FRAME_SECONDS))
        )
        self._pre_roll = collections.deque(maxlen=self.pre_roll_frames)
        self._speech = []
        self._silence_run = 0
        self.speaking = False
        self.noise_floor = MIN_TRIGGER_LEVEL / START_MULTIPLIER

    @property
    def trigger_level(self):
        """Level a frame must exceed to begin an utterance."""
        return max(MIN_TRIGGER_LEVEL, self.noise_floor * START_MULTIPLIER)

    def reset(self):
        """Discard any partial utterance, keeping the learned noise floor."""
        self._pre_roll.clear()
        self._speech = []
        self._silence_run = 0
        self.speaking = False

    def _update_floor(self, level):
        self._floor_window.append(level)
        if len(self._floor_window) < 8:
            return
        # A low percentile tracks the quiet background and ignores speech peaks.
        self.noise_floor = float(np.percentile(np.array(self._floor_window), 20.0))

    def feed(self, frame):
        """Process one frame. Returns an event dictionary."""
        level = frame_level(frame)
        raw = frame.tobytes() if hasattr(frame, "tobytes") else bytes(frame)

        event = {
            "level": level,
            "meter": 0.0,
            "speaking": self.speaking,
            "started": False,
            "ended": False,
            "utterance": None,
            "duration": 0.0,
            "discarded": None,
        }

        if not self.speaking:
            # Only quiet frames should shape the floor estimate.
            self._update_floor(level)
            event["meter"] = level_to_meter(level, self.trigger_level)

            if level >= self.trigger_level:
                self.speaking = True
                self._silence_run = 0
                # Prepend the buffered frames so the word onset is not lost.
                self._speech = list(self._pre_roll)
                self._speech.append(raw)
                event.update(speaking=True, started=True)
            else:
                self._pre_roll.append(raw)
            return event

        # Already inside an utterance.
        event["meter"] = level_to_meter(level, self.trigger_level)
        self._speech.append(raw)

        if level >= self.trigger_level * CONTINUE_RATIO:
            self._silence_run = 0
        else:
            self._silence_run += 1

        too_long = len(self._speech) >= self.max_speech_frames
        if self._silence_run >= self.end_silence_frames or too_long:
            frames = self._speech
            duration = len(frames) * FRAME_SECONDS
            self.reset()
            self._pre_roll.clear()
            event.update(speaking=False, ended=True, duration=duration)
            if len(frames) >= self.min_speech_frames:
                event["utterance"] = frames
            else:
                # A door closing or a keyboard tap, not speech.
                event["discarded"] = "too short"
        return event


def open_stream():
    """Open the microphone. Imported lazily so the module loads without audio."""
    import sounddevice as sd

    return sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=FRAME_SAMPLES,
    )


def _bar(fraction, width=34):
    filled = int(round(fraction * width))
    return "█" * filled + "·" * (width - filled)


def calibrate(seconds=4.0):
    """Measure the room's noise floor. Stay quiet while it runs."""
    print(f"Measuring background noise for {seconds:.0f}s. Please stay quiet…")
    detector = VoiceActivityDetector()
    levels = []
    try:
        with open_stream() as stream:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                frame, _overflow = stream.read(FRAME_SAMPLES)
                level = frame_level(frame[:, 0] if frame.ndim > 1 else frame)
                levels.append(level)
                detector._update_floor(level)
    except Exception as exc:
        print(f"Could not read the microphone: {exc}")
        return 1

    array = np.array(levels) if levels else np.array([0.0])
    print()
    print(f"  frames measured : {len(levels)}")
    print(f"  quiet level     : {np.percentile(array, 20):.0f}")
    print(f"  median          : {np.median(array):.0f}")
    print(f"  peak            : {array.max():.0f}")
    print(f"  speech trigger  : {detector.trigger_level:.0f}")
    print()
    if detector.trigger_level <= MIN_TRIGGER_LEVEL + 1:
        print("This room is quiet; the absolute minimum trigger applies.")
    else:
        print("Trigger raised above the measured background noise.")
    return 0


def monitor():
    """Show a live meter and report each detected utterance.

    This needs no API key and no GUI, which makes it the fastest way to tell
    whether the microphone, the levels, and the detector are behaving.
    """
    detector = VoiceActivityDetector()
    print("Listening. Speak normally. Ctrl+C to stop.")
    print()
    utterances = 0
    try:
        with open_stream() as stream:
            while True:
                frame, _overflow = stream.read(FRAME_SAMPLES)
                mono = frame[:, 0] if frame.ndim > 1 else frame
                event = detector.feed(mono)

                state = "SPEAKING" if event["speaking"] else "listening"
                line = (
                    f"\r[{_bar(event['meter'])}] {event['level']:6.0f} "
                    f"floor {detector.noise_floor:5.0f} "
                    f"trig {detector.trigger_level:5.0f}  {state:9}"
                )
                sys.stdout.write(line)
                sys.stdout.flush()

                if event["started"]:
                    sys.stdout.write("\n  ▶ speech started\n")
                    sys.stdout.flush()
                if event["ended"]:
                    if event["utterance"]:
                        utterances += 1
                        audio = wav_bytes(event["utterance"])
                        sys.stdout.write(
                            f"\n  ■ utterance {utterances}: "
                            f"{event['duration']:.2f}s, {len(audio):,} bytes of WAV\n"
                        )
                    else:
                        sys.stdout.write(
                            f"\n  · ignored ({event['discarded']}, "
                            f"{event['duration']:.2f}s)\n"
                        )
                    sys.stdout.flush()
    except KeyboardInterrupt:
        print()
        print(f"Stopped. {utterances} utterance(s) captured.")
        return 0
    except Exception as exc:
        print()
        print(f"Microphone error: {exc}")
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Muno microphone streaming tools")
    parser.add_argument("--monitor", action="store_true",
                        help="live meter and speech detection")
    parser.add_argument("--calibrate", action="store_true",
                        help="measure the room's noise floor")
    parser.add_argument("--seconds", type=float, default=4.0,
                        help="how long to calibrate for")
    arguments = parser.parse_args(argv)

    if arguments.calibrate:
        return calibrate(arguments.seconds)
    if arguments.monitor:
        return monitor()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
