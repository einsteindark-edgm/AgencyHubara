"""Platform — pipeline de transcripción de audio inbound de WhatsApp.

Composición:

* `port.AudioTranscriptionPort` — interfaz puerto/adaptador puro
* `dtos.TranscriptionRequest`, `TranscriptionResult` — DTOs R-JSON safe
* `meta_media_fetcher.fetch_media_bytes` — descarga del media_id de Meta
* `litellm_adapter.LiteLLMTranscriptionAdapter` — único adapter, usa el
  proxy litellm del proyecto (`API_BASE_LLMLITE`) con Gemini Flash-Lite
  por default
* `composition.get_audio_transcription_port` — factory singleton

Decisión arquitectónica (mayo 2026):
  El proyecto ya estandarizó litellm como proxy unificado para LLMs
  (`API_BASE_LLMLITE` en `src/platform/config.py`). El layer de audio
  reutiliza ese mismo proxy en lugar de tener clientes HTTP custom por
  cada provider STT. Esto le da al operador la posibilidad de cambiar
  el modelo (Gemini Flash-Lite → Flash 2.5 → Flash 3.0 cuando salga,
  o incluso openai/whisper-1) por una sola env var, sin redeploy.

Default por env:

  AUDIO_TRANSCRIPTION_PROVIDER=auto                       # default
  AUDIO_TRANSCRIPTION_MODEL=gemini/gemini-2.5-flash-lite  # default
  AUDIO_TRANSCRIPTION_API_BASE=$API_BASE_LLMLITE          # default (proxy)
  AUDIO_TRANSCRIPTION_API_KEY=                            # opcional si el proxy maneja auth
  GEMINI_API_KEY=...                                      # required si bypass proxy
  WHATSAPP_ACCESS_TOKEN=...                               # required para fetch media

Costo estimado para Hubara (audio español, voice notes WA típicos ≤30s):

  | Modelo                       | $/hora aprox | Notas                       |
  |------------------------------|--------------|-----------------------------|
  | gemini/gemini-2.5-flash-lite | ~$0.014      | default, balance costo/calidad |
  | gemini/gemini-2.5-flash      | ~$0.115      | mejor en audios ruidosos    |
  | openai/whisper-1             | $0.36        | fallback si Gemini falla    |
"""
from src.platform.audio.dtos import (
    TranscriptionRequest,
    TranscriptionResult,
)
from src.platform.audio.port import AudioTranscriptionPort

__all__ = [
    "AudioTranscriptionPort",
    "TranscriptionRequest",
    "TranscriptionResult",
]
