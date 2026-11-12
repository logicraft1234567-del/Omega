from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate

login_manager = LoginManager()
csrf = CSRFProtect()
limiter = Limiter(get_remote_address, default_limits=["600 per hour"], storage_uri="memory://")
migrate = Migrate()
