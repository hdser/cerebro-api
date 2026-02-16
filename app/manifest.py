import json
import os
import hashlib
import requests
from typing import Dict, Any, Optional, List, Tuple
from app.config import settings

class ManifestLoader:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ManifestLoader, cls).__new__(cls)
            cls._instance._models = {}
            cls._instance._etag = None
            cls._instance._last_modified = None
            cls._instance._hash = None
            cls._instance._last_error = None
            cls._instance._load_manifest(allow_fallback=True, conditional=False)
        return cls._instance

    def _hash_bytes(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _load_manifest(self, allow_fallback: bool, conditional: bool) -> bool:
        data = None
        raw_bytes = None
        errors = []
        new_etag = None
        new_last_modified = None
        source = None
        self._last_error = None
        
        # 1. Try URL first
        if settings.DBT_MANIFEST_URL:
            try:
                print(f"🌐 Fetching manifest from {settings.DBT_MANIFEST_URL}...")
                headers = {}
                if conditional:
                    if self._etag:
                        headers["If-None-Match"] = self._etag
                    if self._last_modified:
                        headers["If-Modified-Since"] = self._last_modified
                response = requests.get(settings.DBT_MANIFEST_URL, timeout=30, headers=headers)
                if response.status_code == 304:
                    print("🔄 Manifest not modified (304).")
                    return False
                if response.status_code == 200:
                    raw_bytes = response.content
                    try:
                        data = response.json()
                        source = "url"
                        new_etag = response.headers.get("ETag")
                        new_last_modified = response.headers.get("Last-Modified")
                        print("✅ Manifest downloaded successfully.")
                    except Exception as e:
                        msg = f"❌ Error parsing manifest JSON from URL: {e}"
                        errors.append(msg)
                        print(msg)
                else:
                    msg = f"❌ Failed to download manifest: Status {response.status_code}"
                    errors.append(msg)
                    print(msg)
            except Exception as e:
                msg = f"❌ Error fetching manifest URL: {e}"
                errors.append(msg)
                print(msg)

        # 2. Fallback to local file
        if not data and allow_fallback and os.path.exists(settings.DBT_MANIFEST_PATH):
            try:
                print(f"📂 Loading manifest from local file: {settings.DBT_MANIFEST_PATH}")
                with open(settings.DBT_MANIFEST_PATH, 'rb') as f:
                    raw_bytes = f.read()
                data = json.loads(raw_bytes.decode("utf-8"))
                source = "file"
            except Exception as e:
                msg = f"❌ Error loading local manifest: {e}"
                errors.append(msg)
                print(msg)

        if not data:
            print("⚠️ No manifest loaded. API will define routes but metadata will be missing.")
            if errors:
                self._last_error = " | ".join(errors)
            else:
                self._last_error = "No manifest loaded."
            return False

        new_hash = None
        if raw_bytes is not None:
            new_hash = self._hash_bytes(raw_bytes)
        else:
            new_hash = self._hash_bytes(json.dumps(data, sort_keys=True).encode("utf-8"))

        if self._hash and new_hash == self._hash:
            if source == "file":
                self._etag = None
                self._last_modified = None
            if source == "url":
                if new_etag:
                    self._etag = new_etag
                if new_last_modified:
                    self._last_modified = new_last_modified
            print("🔄 Manifest unchanged (hash match).")
            return False

        # Index models
        new_models: Dict[str, Any] = {}
        for key, node in data.get("nodes", {}).items():
            if node.get("resource_type") == "model":
                name = node.get("name")
                new_models[name] = node

        self._models = new_models
        self._hash = new_hash

        if source == "file":
            self._etag = None
            self._last_modified = None
        if new_etag:
            self._etag = new_etag
        if new_last_modified:
            self._last_modified = new_last_modified

        self._last_error = None
        
        print(f"✅ Loaded {len(self._models)} models from dbt manifest.")
        return True

    def reload_if_changed(self) -> Tuple[bool, Optional[str]]:
        """
        Reload manifest only if the remote source has changed.
        Returns (changed, error_message).
        """
        changed = self._load_manifest(allow_fallback=False, conditional=True)
        if changed:
            return True, None
        if self._last_error:
            return False, self._last_error
        return False, None

    def get_all_models(self) -> List[str]:
        """Return a list of all model names."""
        return list(self._models.keys())

    def get_model(self, model_name: str) -> Optional[Dict[str, Any]]:
        return self._models.get(model_name)

    def get_table_name(self, model_name: str) -> str:
        node = self.get_model(model_name)
        if node:
            schema = node.get("schema", "default")
            alias = node.get("alias", model_name)
            return f"{schema}.{alias}"
        return model_name

    def get_columns(self, model_name: str) -> Dict[str, str]:
        """Returns a dict of column_name -> data_type"""
        node = self.get_model(model_name)
        if not node:
            return {}
        
        cols = {}
        for col_name, col_meta in node.get("columns", {}).items():
            cols[col_name] = col_meta.get("data_type", "String")
        return cols

    def get_tags(self, model_name: str) -> List[str]:
        node = self.get_model(model_name)
        if not node:
            return []
        return node.get("tags", [])

    def model_count(self) -> int:
        return len(self._models)

manifest = ManifestLoader()
