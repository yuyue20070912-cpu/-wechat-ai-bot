import pytest


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_home(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert b"fishyeah" in response.data.lower()


def test_anonymous_identity(client):
    response = client.get("/auth/me")
    assert response.status_code == 200
    assert response.get_json() == {"authenticated": False}


@pytest.mark.parametrize("method,path", [
    ("post", "/chat"),
    ("get", "/account/usage"),
    ("get", "/membership/orders"),
    ("post", "/membership/orders"),
    ("post", "/membership/orders/1/payment-note"),
    ("get", "/membership/payment-qr/wechat"),
    ("post", "/auth/change-password"),
])
def test_protected_routes_require_login(client, method, path):
    response = getattr(client, method)(path)
    assert response.status_code == 401
    assert response.get_json() == {"error": "请先登录"}
