from typing import Any, Dict, Optional, Tuple

import requests

from interaktiv.kyra.api.base import APIBase


class Chat(APIBase):
    def _chat_url(self) -> str:
        gateway_url = (self.gateway_url or "").rstrip("/")
        if not gateway_url:
            return ""

        if gateway_url.endswith("/chat"):
            return gateway_url

        if gateway_url.endswith("/prompts"):
            gateway_url = gateway_url[: -len("/prompts")]

        return f"{gateway_url}/chat"

    def _fallback_chat_url(self) -> str:
        return (self.gateway_url or "").rstrip("/")

    def _get_chat_headers(self, include_content_type: bool = True) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        domain_id = self._get_domain_id()
        if domain_id:
            headers["x-domain-id"] = domain_id
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if include_content_type:
            headers["Content-Type"] = "application/json"
        return headers

    def send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self._chat_url()
        headers = self._get_chat_headers()
        if not headers:
            return {"error": "No headers available"}

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as e:
            fallback = self._fallback_chat_url()
            if fallback and fallback != url:
                message = str(e).lower()
                if "404" in message or "not found" in message:
                    try:
                        response = requests.post(
                            fallback, headers=headers, json=payload, timeout=60
                        )
                        response.raise_for_status()
                        return response.json()
                    except Exception:
                        return {"error": str(e)}
            return {"error": str(e)}
        except requests.Timeout:
            return {"error": "Request timeout - please try again"}
        except requests.ConnectionError:
            return {"error": "Cannot connect to API service"}
        except Exception as e:
            return {"error": f"Request failed: {e}"}

    def stream(
        self, payload: Dict[str, Any]
    ) -> Tuple[Optional[requests.Response], Optional[str]]:
        url = self._chat_url()
        headers = self._get_chat_headers()
        if not headers:
            return None, "No headers available"
        headers["Accept"] = "text/event-stream"

        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                stream=True,
                timeout=60,
            )
            response.raise_for_status()
            return response, None
        except requests.HTTPError as e:
            message = str(e).lower()
            fallback = self._fallback_chat_url()
            if fallback and fallback != url and ("404" in message or "not found" in message):
                try:
                    response = requests.post(
                        fallback,
                        headers=headers,
                        json=payload,
                        stream=True,
                        timeout=60,
                    )
                    response.raise_for_status()
                    return response, None
                except Exception:
                    return None, str(e)
            return None, str(e)
        except requests.Timeout:
            return None, "Request timeout - please try again"
        except requests.ConnectionError:
            return None, "Cannot connect to API service"
        except Exception as e:
            return None, f"Request failed: {e}"
