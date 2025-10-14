from __future__ import annotations

import functools
import io
import json
import logging
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import TCPServer

from PIL import Image

from dynsight._internal.vision.synthetic import build_crops, synthesize_dataset_zip

try:
    import cgi
except ModuleNotFoundError:  # pragma: no cover - fallback for removed cgi module
    cgi = None

logger = logging.getLogger(__name__)


class HTTPRequestHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        pass

    # do_POST must be uppercase
    def do_POST(self) -> None:
        if self.path == "/shutdown":
            self.send_response(200)
            self.end_headers()
            logger.info("Shutdown request received.")
            threading.Thread(target=self.server.shutdown).start()
        elif self.path == "/synthesize":
            self._handle_synthesize()
        else:
            self.send_error(404)

    def _handle_synthesize(self) -> None:
        if cgi is None:
            self.send_error(500, "Synthetic dataset generation is not supported.")
            return

        content_type = self.headers.get("Content-Type")
        if not content_type:
            self.send_error(400, "Missing Content-Type header.")
            return

        environ = {
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": content_type,
        }
        content_length = self.headers.get("Content-Length")
        if content_length:
            environ["CONTENT_LENGTH"] = content_length

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ=environ,
            keep_blank_values=True,
        )

        try:
            params_raw = form.getfirst("params")
            annotations_raw = form.getfirst("annotations")
            if not params_raw:
                msg = "Missing synthesis parameters."
                raise ValueError(msg)
            if not annotations_raw:
                msg = "Missing annotations payload."
                raise ValueError(msg)

            params = json.loads(params_raw)
            annotations = json.loads(annotations_raw)
            if not isinstance(annotations, dict):
                msg = "Annotations payload must be an object."
                raise ValueError(msg)

            num_images = int(params["num_images"])
            width = int(params["width"])
            height = int(params["height"])
            min_objects = int(params["min_objects"])
            max_objects = int(params["max_objects"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self.send_error(400, str(exc))
            return

        image_fields = [
            field
            for field in (form.list or [])
            if field.name == "images" and getattr(field, "filename", None)
        ]

        if not image_fields:
            self.send_error(400, "No images uploaded.")
            return

        images: dict[str, Image.Image] = {}
        for field in image_fields:
            data = field.file.read()
            if not data:
                continue
            try:
                with Image.open(io.BytesIO(data)) as img:
                    images[field.filename] = img.convert("RGB")
            except Exception as exc:  # pragma: no cover - invalid uploads
                logger.warning("Failed to read image %s: %s", field.filename, exc)

        if not images:
            self.send_error(400, "Unable to decode uploaded images.")
            return

        try:
            crops, label_map = build_crops(images, annotations)
            archive = synthesize_dataset_zip(
                crops,
                label_map,
                num_images=num_images,
                width=width,
                height=height,
                min_objects=min_objects,
                max_objects=max_objects,
            )
        except ValueError as exc:
            self.send_error(400, str(exc))
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header(
            "Content-Disposition",
            'attachment; filename="synt_dataset.zip"',
        )
        self.send_header("Content-Length", str(len(archive)))
        self.end_headers()
        self.wfile.write(archive)


class ReusableTCPServer(TCPServer):
    allow_reuse_address = True


def label_tool(port: int = 8888) -> None:
    web_dir = Path(__file__).parent / "label_tool"
    handler = functools.partial(HTTPRequestHandler, directory=str(web_dir))
    with ReusableTCPServer(("", port), handler) as httpd:
        url = f"http://localhost:{port}/index.html"
        logger.info(f"Starting server at {url}")
        webbrowser.open(url)
        httpd.serve_forever()
        httpd.server_close()
        logger.info("Server closed.")
