# backtalk: talk to your Claude Code agent out loud.
# Copyright (C) 2026 Jared Rhodenizer
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ears — mic capture with VAD endpointing, transcribed in-process
by faster-whisper. Local, free, no server, no API key.

record_held() is the hold-to-talk capture (the button is the VAD).
Ears.listen_once() is the legacy open-mic mode: blocks until one
complete utterance is heard, then returns its transcript. Endpointing:
an utterance opens after ~120ms of sustained speech, closes after
`silence_ms` of trailing quiet (config key `vad_silence_ms`, tune
it there rather than here). How readily a frame counts as speech in
the first place is `vad_aggressiveness`. A `gate` callable can suppress
listening (so the open mic ignores the speakers unless barge-in is on).
"""
import os
import pathlib
import platform
import re
import sys
import threading

import numpy as np
import sounddevice as sd
import webrtcvad

from backtalk.config import CFG
from backtalk.vlog import log

RATE = 16000
FRAME_MS = 30
FRAME_LEN = RATE * FRAME_MS // 1000  # samples per frame
OPEN_FRAMES = 4        # ~120ms speech to open an utterance
MAX_UTTER_S = 30
# Sustained speech before `on_speech` fires, in frames. Deliberately
# stricter than OPEN_FRAMES: opening an utterance costs nothing if it
# turns out to be a noise blip, but firing this cuts the agent off
# mid-word, so it should not happen on 120ms of anything. 8 frames is
# 240ms, fast enough to feel immediate when you talk over him.
BARGE_FRAMES = 8

_NONSPEECH = re.compile(r"[\[(][^\])]*[\])]")


def _dbfs(pcm) -> float:
    """RMS of int16 audio in dBFS. -inf for digital silence."""
    if len(pcm) == 0:
        return float("-inf")
    r = float(np.sqrt(np.mean((pcm.astype(np.float64) / 32768.0) ** 2)))
    return float("-inf") if r <= 0 else 20.0 * float(np.log10(r))

_model = None
_model_lock = threading.Lock()
_backend = None          # "mlx" once the GPU path loads, else "faster-whisper"


def _apple_gpu_available() -> bool:
    """Apple Silicon only. CTranslate2, the runtime under faster-whisper,
    has no Metal backend, so on every Mac it transcribes on the CPU while
    the GPU sits idle. mlx-whisper runs the SAME model on the GPU.

    Measured on an M4 Max, small.en, a 6.5s clip, warm: 0.88s on the CPU
    path against 0.12s on the GPU, with a character-identical transcript
    on three of four test clips and a two-comma difference on the fourth.

    Not a second product and not a user-facing choice: same model name
    from the same config key, same text out, one platform finally running
    it properly. Anything that is not an Apple Silicon Mac keeps
    faster-whisper, which already uses CUDA wherever it exists."""
    if sys.platform != "darwin" or platform.machine() != "arm64":
        return False
    try:
        import mlx_whisper                       # noqa: F401
    except ImportError:
        return False
    return True


def _mlx_repo(model_name: str) -> str:
    """A faster-whisper model name -> its MLX conversion on the Hub."""
    return f"mlx-community/whisper-{model_name}-mlx"


_mic_logged = None


def _input_device():
    """Which microphone to open, by preference rather than by pin.

    `mic_device` may be null (whatever Windows calls the default), a name
    fragment, or a list of fragments tried in order. The first fragment
    matching a device with input channels wins; if none match, this
    returns None and sounddevice takes the system default. That is what
    makes one config work in two places: the good desk microphone at
    home, the built-in one on the road, with no edit in between.

    It also LOGS the choice, once per session or whenever it changes.
    Windows silently left the default on the laptop's internal mic after
    a USB microphone was plugged in on 2026-08-27, and hours were spent
    chasing that as a transcription problem. A named device in the log
    would have ended it immediately."""
    global _mic_logged
    want = CFG.get("mic_device")
    chosen, name = None, None
    if want:
        frags = [want] if isinstance(want, str) else list(want)
        try:
            devs = sd.query_devices()
        except Exception:
            devs = []
        for frag in frags:
            f = str(frag).lower().strip()
            if not f:
                continue
            for i, d in enumerate(devs):
                if d.get("max_input_channels", 0) > 0 \
                        and f in str(d.get("name", "")).lower():
                    chosen, name = i, d["name"]
                    break
            if chosen is not None:
                break
    if name is None:
        try:
            di = sd.default.device[0]
            name = f"{sd.query_devices()[di]['name']} (system default)"
        except Exception:
            name = "system default"
    if name != _mic_logged:
        _mic_logged = name
        log(f"[ears] microphone: {name}")
    return chosen


def check_input_level(secs: float = 0.4) -> float:
    """Sample the microphone briefly at startup and say so when it is dead.

    A muted USB microphone is indistinguishable from a quiet room in
    every downstream signal: webrtcvad still counts frames on its noise,
    Silero correctly finds no speech, and the result is an agent that
    transcribes nothing while appearing to work perfectly. That cost
    several hours on 2026-08-27, and the tell was available in 400ms:
    a live condenser in a real room floors around -60 to -70 dBFS from
    ambient and self-noise, while a muted device reads about -97.

    Judged on the FLOOR, deliberately, not on whether speech appears.
    The floor answers "is this device alive" without anyone needing to
    speak at the right moment. Never raises: a machine with no input at
    all should not stop the voice line from starting."""
    try:
        rec = sd.rec(int(RATE * secs), samplerate=RATE, channels=1,
                     dtype="float32", device=_input_device())
        sd.wait()
        x = rec[:, 0]
    except Exception as e:
        log(f"[ears] could not sample the input: {e!r}")
        return float("nan")
    r = float(np.sqrt(np.mean(x ** 2)))
    db = -120.0 if r <= 0 else 20.0 * float(np.log10(r))
    if db < -85:
        log(f"[ears] WARNING: input reads {db:.0f} dBFS, which is "
            f"silence, not a quiet room. The microphone is muted or its "
            f"gain is at minimum. Nothing you say will be heard.")
    elif db < -75:
        log(f"[ears] input floor {db:.0f} dBFS, suspiciously quiet")
    else:
        log(f"[ears] input floor {db:.0f} dBFS")
    return db


def _add_cuda_dll_dirs():
    """Make the pip-installed CUDA runtime findable on Windows.

    CTranslate2, the runtime under faster-whisper, links cuBLAS and cuDNN
    by name. The `nvidia-*-cu12` wheels put those DLLs in
    site-packages/nvidia/<lib>/bin, and Windows does not search that
    directory: since Python 3.8 the DLL search path ignores PATH for
    extension modules, so an installed cuDNN is invisible and the failure
    reads as `Library cublas64_12.dll is not found`, which looks like a
    missing install rather than a missing search path.

    Returns the directories added, or [] on any platform or layout where
    this does not apply. Never raises: a machine with no wheels installed
    simply falls through and CTranslate2 reports the honest error."""
    if sys.platform != "win32":
        return []
    roots = []
    try:
        # `nvidia` is a PEP 420 namespace package: it has no __init__.py,
        # so __file__ is None and only __path__ locates it. Reading
        # __file__ here returns None and finds nothing, silently.
        import nvidia
        roots = [pathlib.Path(p) for p in getattr(nvidia, "__path__", [])]
    except Exception:
        pass
    if not roots:
        import site
        cands = list(site.getsitepackages())
        try:
            cands.append(site.getusersitepackages())
        except Exception:
            pass
        roots = [pathlib.Path(c) / "nvidia" for c in cands]
    added, paths = [], []
    for root in roots:
        if not root.is_dir():
            continue
        for bin_dir in sorted(root.glob("*/bin")):
            if bin_dir.is_dir():
                try:
                    os.add_dll_directory(str(bin_dir))
                except OSError:
                    pass
                paths.append(str(bin_dir))
                added.append(bin_dir.parent.name)
    if paths:
        # add_dll_directory alone is not enough. It only affects loads
        # that use LOAD_LIBRARY_SEARCH_*, and CTranslate2 asks for
        # "cublas64_12.dll" by bare name through the plain LoadLibrary
        # search order, which consults PATH and not the added
        # directories. Measured on this machine 2026-08-27: with the
        # directories added and PATH untouched, it still raised
        # `Library cublas64_12.dll is not found or cannot be loaded`.
        # Both are set because each covers a different loader.
        os.environ["PATH"] = os.pathsep.join(
            paths + [os.environ.get("PATH", "")])
    return added


def warm():
    """Load the STT model (first call downloads it to the HF cache).
    Called at startup while the greeting plays, so the first real
    utterance doesn't pay the load."""
    global _model, _backend
    with _model_lock:
        if _model is None:
            if _apple_gpu_available():
                import mlx_whisper
                repo = _mlx_repo(CFG["stt_model"])
                log(f"[ears] loading {CFG['stt_model']} on the Apple GPU...")
                # This API has no separate load call: the first transcribe
                # pulls and caches the weights. Warm on a beat of silence so
                # the first real utterance does not pay for it.
                mlx_whisper.transcribe(np.zeros(RATE // 10, dtype=np.float32),
                                       path_or_hf_repo=repo, language="en",
                                       verbose=None)
                _model, _backend = repo, "mlx"
            else:
                if str(CFG["stt_device"]).startswith("cuda"):
                    dirs = _add_cuda_dll_dirs()
                    log(f"[ears] cuda dll dirs: "
                        f"{', '.join(dirs) if dirs else 'none found'}")
                from faster_whisper import WhisperModel
                log(f"[ears] loading {CFG['stt_model']} "
                    f"({CFG['stt_device']}/{CFG['stt_compute']})...")
                _model = WhisperModel(CFG["stt_model"],
                                      device=CFG["stt_device"],
                                      compute_type=CFG["stt_compute"])
                _backend = "faster-whisper"
                if str(CFG["stt_device"]).startswith("cuda"):
                    # Construction is lazy: CTranslate2 does not touch
                    # cuBLAS until the first encode. Left alone, a broken
                    # CUDA runtime fails on his first sentence and reads
                    # as a mic fault, which is exactly how the 2026-08-27
                    # cublas64_12 hunt started. Force the GPU path here,
                    # on a beat of silence, and degrade to the CPU rather
                    # than leaving him unable to be heard at all.
                    try:
                        segs, _ = _model.transcribe(
                            np.zeros(RATE, dtype=np.float32), language="en")
                        list(segs)              # the generator does the work
                    except Exception as e:
                        log(f"[ears] CUDA unusable ({type(e).__name__}: "
                            f"{str(e)[:90]}) - falling back to cpu/int8. "
                            f"The voice line still works, just slower.")
                        _model = WhisperModel(CFG["stt_model"],
                                              device="cpu",
                                              compute_type="int8")
            log(f"[ears] model ready ({_backend})")
    return _model


def transcribe(pcm: np.ndarray) -> str:
    """int16 mono 16kHz -> text. Bracketed non-speech markers that
    whisper emits ([BLANK_AUDIO], [SIGHS], (coughs)...) are stripped;
    if nothing remains, it was silence.

    Two guards against whisper's silence hallucinations, which are the
    reason a muted microphone could hold a conversation. Fed audio with
    no speech in it, whisper writes a caption phrase from its training
    data: "You", "Thanks for watching!", "Music Music". Measured here on
    2026-08-27, digital silence returned 'You' with a no_speech_prob of
    0.90, and 120Hz hum returned 'You' at 0.95, while real speech sits
    near 0.00. So: Silero VAD decides what is even decoded, and any
    segment whisper doubts more than stt_no_speech_max is dropped. Both
    are config keys because both can be wrong about quiet real speech.
    An empty return is a normal outcome, not an error; the caller drops
    it and keeps listening."""
    model = warm()
    audio = pcm.astype(np.float32) / 32768.0
    lang = "en" if CFG["stt_model"].endswith(".en") else None
    if _backend == "mlx":
        import mlx_whisper
        text = mlx_whisper.transcribe(audio, path_or_hf_repo=model,
                                      temperature=0.0, language=lang,
                                      verbose=None)["text"].strip()
    else:
        segments, _ = model.transcribe(
            audio, temperature=0.0, language=lang,
            vad_filter=bool(CFG["stt_vad_filter"]),
            # each utterance is decoded on its own, so there is no prior
            # worth conditioning on, and leaving it True is what lets a
            # hallucinated token feed itself into "Music Music Music"
            condition_on_previous_text=False)
        limit = float(CFG["stt_no_speech_max"])
        segs = list(segments)          # materialise once, inspected below
        text = "".join(s.text for s in segs
                       if s.no_speech_prob <= limit).strip()
        if not text:
            # Say WHICH guard threw it away. Zero segments means Silero
            # found no speech and whisper never saw the audio; segments
            # present means the confidence filter rejected them. Without
            # this the two are indistinguishable and the only remedy is
            # guesswork.
            if not segs:
                log("[ears] nothing kept: silero found no speech in the "
                    "clip (stt_vad_filter)")
            else:
                worst = max(s.no_speech_prob for s in segs)
                log(f"[ears] nothing kept: {len(segs)} segment(s), worst "
                    f"no_speech_prob {worst:.2f} against a limit of "
                    f"{limit} (stt_no_speech_max); text was "
                    f"{''.join(s.text for s in segs).strip()[:80]!r}")
    return _NONSPEECH.sub("", text).strip()


class Ears:
    def __init__(self, aggressiveness: int | None = None,
                 silence_ms: int | None = None):
        if aggressiveness is None:
            aggressiveness = int(CFG["vad_aggressiveness"])
        self.vad = webrtcvad.Vad(aggressiveness)
        if silence_ms is None:
            silence_ms = int(CFG["vad_silence_ms"])
        self.silence_frames = silence_ms // FRAME_MS

    def listen_once(self, gate=None, timeout_s: float | None = None,
                    abort=None, on_speech=None) -> str | None:
        """Block until one utterance completes; return transcript
        (or None on timeout). An `abort` callable is checked every
        frame; returning True closes the mic and returns None, which
        is how a live switch back to push-to-talk shuts the open mic
        down promptly instead of after one more utterance.

        `on_speech` is called ONCE per utterance, from this thread, as
        soon as BARGE_FRAMES of speech have accumulated. It exists
        because the return value is far too late to interrupt with:
        this does not return until the endpointer fires, which is
        `vad_silence_ms` AFTER he stops talking, so a reply cancelled on
        the transcript is cancelled once he has already finished
        speaking and the agent has finished answering. Anything that
        needs to react to him STARTING to speak hangs off this."""
        frames: list[np.ndarray] = []
        ring: list[np.ndarray] = []   # pre-roll so the first syllable survives
        speech_run = 0
        silence_run = 0
        speech_total = 0
        in_utterance = False
        barged = False           # on_speech fires at most once per utterance
        quiet_logged = False
        elapsed = 0.0

        with sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                            blocksize=FRAME_LEN,
                            device=_input_device()) as stream:
            while True:
                block, _ = stream.read(FRAME_LEN)
                elapsed += FRAME_MS / 1000
                if abort and abort():
                    return None
                if timeout_s and elapsed > timeout_s and not in_utterance:
                    return None
                mono = block[:, 0].copy()
                if gate and gate():
                    # speakers are talking and barge-in isn't on: ignore
                    ring.clear()
                    continue
                is_speech = self.vad.is_speech(mono.tobytes(), RATE)
                if not in_utterance:
                    ring.append(mono)
                    if len(ring) > 8:
                        ring.pop(0)
                    speech_run = speech_run + 1 if is_speech else 0
                    if speech_run >= OPEN_FRAMES:
                        in_utterance = True
                        frames = ring[:]
                        silence_run = 0
                else:
                    frames.append(mono)
                    if is_speech:
                        speech_total += 1
                        silence_run = 0
                        if on_speech is not None and not barged \
                                and speech_total >= BARGE_FRAMES:
                            # webrtcvad ALONE is not enough to justify
                            # cutting him off. It is the permissive
                            # detector: it counted 274 "speech" frames on
                            # a clip Silero then ruled had no speech in
                            # it at all, and firing on that stopped him
                            # mid-sentence repeatedly. Silero cannot help
                            # here, it only sees the finished clip, so
                            # the second opinion is level. Real speech on
                            # this rig sits near -33 dBFS; the floor is
                            # -97. Anything in between is not him.
                            level = _dbfs(np.concatenate(frames))
                            floor = float(CFG["barge_min_dbfs"])
                            if level >= floor:
                                barged = True
                                log(f"[ears] barge-in at {level:.0f} dBFS")
                                try:
                                    on_speech()
                                except Exception as e:
                                    log(f"[ears] on_speech failed: {e!r}")
                            elif not quiet_logged:
                                # once per utterance, or a noisy room
                                # writes a line every 30ms
                                quiet_logged = True
                                log(f"[ears] barge suppressed: "
                                    f"{level:.0f} dBFS, under {floor:.0f}")
                    else:
                        silence_run += 1
                    if silence_run >= self.silence_frames or \
                       len(frames) * FRAME_MS / 1000 > MAX_UTTER_S:
                        if speech_total < 8:
                            # <240ms of actual speech: a noise blip, not
                            # a sentence — keep listening
                            in_utterance = False
                            frames, ring = [], []
                            speech_run = speech_total = 0
                            barged = quiet_logged = False
                            continue
                        secs = len(frames) * FRAME_MS / 1000
                        text = transcribe(np.concatenate(frames))
                        if not text:
                            # An utterance was captured and yielded
                            # nothing. Usually that is the silence
                            # guards doing their job on room noise, but
                            # it is also exactly what real speech being
                            # wrongly discarded looks like, and without
                            # this line the two are indistinguishable
                            # from the log: both simply show nothing.
                            log(f"[ears] {secs:.1f}s captured, "
                                f"{speech_total} speech frames, "
                                f"transcribed to nothing")
                        return text


def record_held(is_held, max_s: float = 60.0, min_s: float = 0.25) -> str | None:
    """Hold-to-talk capture: record raw audio while is_held() is True,
    then transcribe. The button is the VAD — no endpointing. Returns
    None for taps shorter than min_s (accidental presses)."""
    frames: list[np.ndarray] = []
    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                        blocksize=FRAME_LEN,
                        device=_input_device()) as stream:
        while is_held() and len(frames) * FRAME_MS / 1000 < max_s:
            block, _ = stream.read(FRAME_LEN)
            frames.append(block[:, 0].copy())
        # a small tail so the last word isn't clipped at release
        for _ in range(6):
            block, _ = stream.read(FRAME_LEN)
            frames.append(block[:, 0].copy())
    if len(frames) * FRAME_MS / 1000 < min_s:
        return None
    return transcribe(np.concatenate(frames))


if __name__ == "__main__":
    import time
    print("[ears] listening — say something...", flush=True)
    ears = Ears()
    start = time.time()
    while time.time() - start < 30:
        text = ears.listen_once(timeout_s=30 - (time.time() - start))
        if text:
            print(f"[ears] heard: {text!r}", flush=True)
            break
        if text is None:
            print("[ears] timed out with no speech", flush=True)
            break
        print("[ears] (noise/empty — still listening)", flush=True)
