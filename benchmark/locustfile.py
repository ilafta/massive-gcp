"""
Locust file pour TinyInsta - teste l'endpoint /api/timeline.

Chaque utilisateur Locust simule un utilisateur distinct du dataset.
Variables d'environnement:
  USER_PREFIX : préfixe des noms d'utilisateurs (défaut: 'user')
  NB_USERS    : nombre d'utilisateurs dans le dataset (défaut: 1000)
"""
import os
import threading
from locust import HttpUser, task, between, events

USER_PREFIX = os.environ.get("USER_PREFIX", "user")
NB_USERS = int(os.environ.get("NB_USERS", "1000"))

_lock = threading.Lock()
_counter = [0]


class TimelineUser(HttpUser):
    wait_time = between(0.5, 2.0)

    def on_start(self):
        with _lock:
            idx = _counter[0] % NB_USERS
            _counter[0] += 1
        self.username = f"{USER_PREFIX}{idx + 1}"

    @task
    def get_timeline(self):
        with self.client.get(
            f"/api/timeline?user={self.username}",
            name="/api/timeline",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")
