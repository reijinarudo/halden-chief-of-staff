@echo off
rem face-widget launcher (Windows).
rem   run.bat                    the configured face
rem   run.bat --face radial-widget   override for one launch
rem
rem pythonw, not python: this is a GUI with no console output worth
rem watching, and a console window would defeat the point of a widget
rem that is supposed to be invisible except for the ring.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" widget.py %*
