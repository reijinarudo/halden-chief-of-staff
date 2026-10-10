@echo off
rem fullstack-agent: give your AI a full stack — memory, voice, face, hands.
rem Copyright (C) 2026 Jared Rhodenizer
rem
rem This program is free software: you can redistribute it and/or modify
rem it under the terms of the GNU Affero General Public License as published
rem by the Free Software Foundation, either version 3 of the License, or
rem (at your option) any later version.
rem
rem This program is distributed in the hope that it will be useful,
rem but WITHOUT ANY WARRANTY; without even the implied warranty of
rem MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
rem GNU Affero General Public License for more details.
rem
rem You should have received a copy of the GNU Affero General Public License
rem along with this program. If not, see <https://www.gnu.org/licenses/>.
rem
rem SPDX-License-Identifier: AGPL-3.0-or-later

rem Starts the agent's pieces. Each server gets its own window;
rem close the windows (or this one for the voice) to stop.
rem   start.bat          everything installed
rem   start.bat voice    the voice and the face (no hands)
rem   start.bat hands    the voice and the hands board (no face)

cd /d "%~dp0.."

rem The face is a desktop widget on this machine, not a browser tab. Its
rem run.bat starts the visualizer server itself when 8790 is dead, and
rem --quiet makes a launch beside an already-running widget a no-op rather
rem than a dialog. The ai-visualizer branch is the original behaviour, kept
rem for a checkout that has no face-widget folder.
if not "%1"=="hands" (
  if exist "face-widget\run.bat" (
    echo   face:  starting the desktop widget
    call "face-widget\run.bat" --quiet
    rem call returns with the cwd inside face-widget; the voice block below
    rem expects the agent root.
    cd /d "%~dp0.."
  ) else if exist "ai-visualizer\" (
    echo   face:  starting
    start "agent face" cmd /c "cd ai-visualizer && run.bat"
  )
)

if exist "barehands\" if not "%1"=="voice" (
  echo   hands: starting
  rem if errorlevel reads the where result at run time. A percent-style
  rem check here would expand when this block is parsed and test a stale value.
  where py >nul 2>nul
  if errorlevel 1 (
    start "agent hands" cmd /c "cd barehands && python server.py"
  ) else (
    start "agent hands" cmd /c "cd barehands && py server.py"
  )
)

if exist "backtalk\" (
  rem Single-instance guard, ported from run.sh, which has had it since
  rem day one under the comment "One body, one mouth." A stale voice
  rem session answers the same microphone as a fresh launch: two
  rem processes, one input device, and the newcomer dies silently
  rem because it cannot open it. That happened on 2026-08-27 and read
  rem as the new session simply refusing to speak.
  rem The pattern is written backtalk[.]main, not backtalk\.main, and that
  rem is not a style choice. This command's OWN command line contains the
  rem pattern, so a literal one matches the process doing the matching and
  rem it kills itself. run.sh has used the bracket form for the same
  rem reason since day one. $PID is excluded as well, belt and braces.
  powershell -NoProfile -Command "$p = @(Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'backtalk[.]main' }); if ($p.Count) { Write-Host '  voice: replaced a previous voice session'; $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }" 2>nul
  rem give the old process a moment to release the mic and speakers
  ping -n 2 127.0.0.1 >nul 2>nul
  echo   voice: starting in this window. Close it to hang up.
  cd backtalk
  rem Self-repair: reconcile the voice line's packages before launch
  rem (fast when current; heals a half-installed environment).
  uv sync -q --inexact >nul 2>nul
  uv run python -m backtalk.main
  rem A clean goodbye exits 0 and the window may close. An error exits
  rem nonzero, and the window HOLDS so the message can be read.
  if errorlevel 1 (
    echo.
    echo   The voice line stopped with an error. The message is above.
    echo   The log lives in backtalk\logs\backtalk.log
    pause
  )
)
