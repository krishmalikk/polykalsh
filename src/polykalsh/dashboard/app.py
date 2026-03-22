"""
Flask application factory for the Polykalsh dashboard.

Provides a web interface for monitoring both trading systems:
- Kalshi AI Trading Bot
- Polymarket Copy Trading Bot
"""

from flask import Flask, render_template
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

from polykalsh.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> Flask:
    """
    Create and configure the Flask application.

    Args:
        settings: Application settings (uses default if not provided)

    Returns:
        Configured Flask application
    """
    settings = settings or get_settings()

    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    # Configuration
    app.config["SECRET_KEY"] = settings.dashboard.password or "dev-secret-key"
    app.config["SETTINGS"] = settings

    # Database
    engine = create_engine(settings.database_url)
    SessionLocal = sessionmaker(bind=engine)
    app.config["DB_SESSION"] = scoped_session(SessionLocal)

    # Register blueprints
    from polykalsh.dashboard.routes.main import main_bp
    from polykalsh.dashboard.routes.kalshi import kalshi_bp
    from polykalsh.dashboard.routes.polymarket import polymarket_bp
    from polykalsh.dashboard.routes.advisor import advisor_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(kalshi_bp, url_prefix="/kalshi")
    app.register_blueprint(polymarket_bp, url_prefix="/polymarket")
    app.register_blueprint(advisor_bp, url_prefix="/advisor")

    # Error handlers
    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("errors/500.html"), 500

    # Context processors
    @app.context_processor
    def inject_settings():
        return {
            "kalshi_enabled": settings.hybrid_trading.enabled,
            "copytrader_enabled": settings.copy_trader.enabled,
        }

    # Teardown
    @app.teardown_appcontext
    def shutdown_session(exception=None):
        db = app.config.get("DB_SESSION")
        if db:
            db.remove()

    return app


def run_dashboard(
    host: str = "0.0.0.0",
    port: int | None = None,
    debug: bool = False,
) -> None:
    """
    Run the dashboard server.

    Args:
        host: Host to bind to
        port: Port to bind to (uses config if not provided)
        debug: Enable debug mode
    """
    settings = get_settings()
    port = port or settings.dashboard.port

    app = create_app(settings)
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_dashboard(debug=True)
