"""Thin client for the team's VSS retrieval backend.

Routes mirror the starter repo skills in vast-builders-challenge/.cursor/skills/retrieval/.
Credentials come from the environment only, never from files in this repo.
"""

import os

import httpx


def _env(*names, default=""):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


class VSSClient:
    def __init__(self, base_url=None, username=None, password=None, timeout=60.0):
        # VSS_* is what deploy/deploy.sh injects into the pod; INGRESS_URL/USERNAME/PASSWORD
        # are what the workshop VM already exports.
        self.base_url = (base_url or _env("VSS_URL", "INGRESS_URL")).rstrip("/")
        self.username = username or _env("VSS_USERNAME", "USERNAME")
        self.password = password or _env("VSS_PASSWORD", "PASSWORD")
        self.timeout = timeout
        self._token = None
        self._http = httpx.Client(timeout=timeout)

    @property
    def configured(self):
        return bool(self.base_url and self.username and self.password)

    def _url(self, path):
        return f"{self.base_url}/api/v1/{path.lstrip('/')}"

    def login(self):
        resp = self._http.post(
            self._url("auth/login"),
            json={"username": self.username, "password": self.password},
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        return self._token

    @property
    def token(self):
        return self._token or self.login()

    def _request(self, method, path, **kwargs):
        if not self.configured:
            raise RuntimeError("VSS is not configured: set VSS_URL/VSS_USERNAME/VSS_PASSWORD")
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {self.token}"}
            resp = self._http.request(method, self._url(path), headers=headers, **kwargs)
            if resp.status_code == 401 and attempt == 0:
                self._token = None  # token expired, log in again once
                continue
            resp.raise_for_status()
            return resp.json()

    # ---- search / Q&A ----

    def search(self, query, top_k=15, min_similarity=0.3, **filters):
        """filters: tags, time_filter, metadata_filters, llm_top_n, include_public, ..."""
        body = {"query": query, "top_k": top_k, "min_similarity": min_similarity, **filters}
        return self._request("POST", "search", json=body)

    def search_and_answer(self, query, top_k=10, **filters):
        body = {"query": query, "top_k": top_k, **filters}
        return self._request("POST", "agent/search-and-answer", json=body)

    def ask(self, question, original_video=None, top_k=10):
        body = {"question": question, "top_k": top_k}
        if original_video:
            body["original_video"] = original_video
        return self._request("POST", "agent/ask", json=body)

    # ---- videos ----

    def explore(self, limit=48, offset=0, scope="all", **params):
        return self._request(
            "GET", "videos/explore", params={"scope": scope, "limit": limit, "offset": offset, **params}
        )

    def segment_metadata(self, source):
        return self._request("GET", "videos/metadata", params={"source": source})

    def detections(self, source):
        """YOLO bbox sidecar for one segment. Raises HTTPStatusError(404) if none exists."""
        return self._request("GET", "videos/detections", params={"source": source})

    def synthesize(self, original_video, question="Summarize what happens in this video", max_segments=40):
        body = {"original_video": original_video, "question": question, "max_segments": max_segments}
        return self._request("POST", "videos/synthesize", json=body)

    def open_stream(self, source, range_header=None):
        """Open a streaming playback response. Caller must close() it.

        The token goes in the query string (backend requirement), so this stays server-side
        and the browser only ever talks to our /api/stream proxy.
        """
        headers = {"Range": range_header} if range_header else {}
        request = self._http.build_request(
            "GET",
            self._url("videos/stream"),
            params={"source": source, "token": self.token},
            headers=headers,
            timeout=None,
        )
        return self._http.send(request, stream=True)

    # ---- metadata / stats ----

    def metadata_schema(self):
        return self._request("GET", "metadata/schema")

    def metadata_values(self, field, prefix="", limit=50):
        return self._request("GET", "metadata/values", params={"field": field, "prefix": prefix, "limit": limit})

    def dashboard_stats(self, scope="all"):
        return self._request("GET", "dashboard/stats", params={"scope": scope})
