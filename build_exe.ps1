# Run after installing project dependencies and PyInstaller.
python -m pip install pyinstaller
pyinstaller --noconfirm --clean --onefile --windowed --name Muno pet.py

Write-Host "Built: dist\\Muno.exe"
