from rest_framework.throttling import SimpleRateThrottle

class LoginPerAccountThrottle(SimpleRateThrottle):
    scope = "login_account"

    def get_cache_key(self, request, view):
        username = request.data.get("username") 

        if not username:
            return None 

        return f"login_account_{username.lower()}"


class LoginIPThrottle(SimpleRateThrottle):
    scope = "login_ip"

    def get_cache_key(self, request, view):
        return self.get_ident(request)
