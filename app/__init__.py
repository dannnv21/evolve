import logging
from typing import Optional

from flask import Flask

from .cli import register_commands
from .config import Config
from .db import close_db
from .errors import register_error_handlers
from .http import ApiJSONProvider

API_PREFIX = "/api/v1"


def create_app(config_object: Optional[object] = None) -> Flask:
    app = Flask(__name__)
    app.json_provider_class = ApiJSONProvider
    app.json = ApiJSONProvider(app)
    app.config.from_object(config_object or Config)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s · %(message)s")
    app.teardown_appcontext(close_db)
    register_error_handlers(app)
    _register_blueprints(app)
    register_commands(app)

    @app.after_request
    def add_cors_headers(response):
        response.headers.setdefault("Access-Control-Allow-Origin", "*")
        response.headers.setdefault("Access-Control-Allow-Headers", "Authorization, Content-Type")
        response.headers.setdefault("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        return response

    return app


def _register_blueprints(app: Flask) -> None:
    from .routes import admin, client, files

    for blueprint in (*client.BLUEPRINTS, *admin.BLUEPRINTS, files.bp):
        app.register_blueprint(blueprint, url_prefix=API_PREFIX + (blueprint.url_prefix or ""))
