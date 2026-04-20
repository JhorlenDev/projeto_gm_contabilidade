from __future__ import annotations

import site
import sys


user_site = site.getusersitepackages()
if isinstance(user_site, str) and user_site and user_site not in sys.path:
    sys.path.append(user_site)
