import requests


class ViOTP:
    def __init__(self, token: str):
        self.token = token
        self.base_url = "https://api.viotp.com"

    def _get(self, endpoint, params=None):
        if params is None:
            params = {}
        params["token"] = self.token
        try:
            response = requests.get(f"{self.base_url}{endpoint}", params=params, timeout=30)
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"status_code": -1, "success": False, "message": str(e), "data": None}

    def get_balance(self):
        return self._get("/users/balance")

    def get_networks(self):
        return self._get("/networks/get")

    def get_services(self, country: str = ""):
        params = {}
        if country:
            params["country"] = country
        return self._get("/service/getv2", params)

    def request_service(
        self,
        service_id: int,
        country: str = "",
        network: str = "",
        prefix: str = "",
        except_prefix: str = "",
        number: str = "",
    ):
        params = {"serviceId": service_id}
        if country:
            params["country"] = country
        if network:
            params["network"] = network
        if prefix:
            params["prefix"] = prefix
        if except_prefix:
            params["exceptPrefix"] = except_prefix
        if number:
            params["number"] = number
        return self._get("/request/getv2", params)

    def get_session(self, request_id: str):
        return self._get("/session/getv2", {"requestId": request_id})

    def get_history(
        self,
        service_id: int,
        status: int = None,
        limit: int = 100,
        from_date: str = "",
        to_date: str = "",
    ):
        params = {"service": service_id, "limit": limit}
        if status is not None:
            params["status"] = status
        if from_date:
            params["fromDate"] = from_date
        if to_date:
            params["toDate"] = to_date
        return self._get("/session/historyv2", params)
