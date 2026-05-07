"""
Orchestration des benchmarks TinyInsta.

Utilisation:
    python benchmark/benchmark.py --app-url https://VOTRE_PROJET.uc.r.appspot.com

Options:
    --app-url     URL de l'application déployée (obligatoire)
    --duration    Durée de chaque test Locust en secondes (défaut: 60)
    --skip-seed   Ne pas re-seeder avant les tests
    --only-conc   Lancer uniquement l'expérience de concurrence
    --only-fanout Lancer uniquement l'expérience de fanout
"""
from __future__ import annotations
import argparse
import csv
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LOCUSTFILE = Path(__file__).parent / "locustfile.py"
SEEDSCRIPT = Path(__file__).parent / "seed_fast.py"
OUT_DIR = Path(__file__).parent.parent / "out"

CONC_PARAMS = [1, 10, 20, 50, 100, 1000]
FANOUT_PARAMS = [20, 40, 60]
NB_RUNS = 3  # modifiable via --runs
NB_USERS = 1000


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def seed(posts_per_user: int, follows: int, clear: bool = True) -> None:
    print(f"\n[SEED] posts_per_user={posts_per_user}, follows={follows}, clear={clear}")
    cmd = [
        sys.executable, str(SEEDSCRIPT),
        "--users", str(NB_USERS),
        "--posts-per-user", str(posts_per_user),
        "--follows", str(follows),
    ]
    if clear:
        cmd.append("--clear")
    result = subprocess.run(cmd, text=True)
    if result.returncode != 0:
        print("[SEED] ERREUR – le seed a échoué !")
        sys.exit(1)
    print("[SEED] OK\n")


def get_instance_count() -> int:
    """Compte les instances App Engine actives."""
    try:
        result = subprocess.run(
            ["gcloud", "app", "instances", "list", "--format=value(id)"],
            capture_output=True, text=True, timeout=30
        )
        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        return len(lines)
    except Exception as e:
        print(f"[WARN] Impossible de compter les instances: {e}")
        return -1


def kill_all_instances(wait_s: int = 20) -> int:
    """
    Force un cold start en stoppant puis redémarrant la version active.
    gcloud app instances delete ne fonctionne pas pour l'auto-scaling F1 ;
    stop/start de version est le seul moyen fiable.
    """
    try:
        # Récupère la version en cours de service
        result = subprocess.run(
            ["gcloud", "app", "versions", "list",
             "--service=default",
             "--filter=SERVING_STATUS=SERVING",
             "--format=value(id)"],
            capture_output=True, text=True, timeout=30
        )
        version_id = result.stdout.strip().split("\n")[0].strip()
        if not version_id:
            print("  [kill] Version active introuvable – on continue sans reset.")
            return -1

        print(f"  [kill] Arrêt version '{version_id}'...")
        r = subprocess.run(
            ["gcloud", "app", "versions", "stop", version_id,
             "--service=default", "--quiet"],
            capture_output=True, text=True, timeout=60
        )
        if r.returncode != 0:
            print(f"  [WARN] stop échoué: {r.stderr.strip()[:120]}")
        time.sleep(3)

        print(f"  [kill] Redémarrage version '{version_id}'...")
        subprocess.run(
            ["gcloud", "app", "versions", "start", version_id,
             "--service=default", "--quiet"],
            capture_output=True, timeout=60
        )

        print(f"  [kill] Instances réinitialisées. Attente {wait_s}s...")
        time.sleep(wait_s)
        return 1

    except Exception as e:
        print(f"  [WARN] kill_all_instances: {e}")
        return -1


def run_locust(app_url: str, concurrent_users: int, duration_s: int,
               nb_users: int = NB_USERS) -> tuple[float, int]:
    """
    Lance Locust en mode headless et retourne (avg_response_ms, nb_failures).
    """
    spawn_rate = min(concurrent_users, 50)  # rampe progressive

    with tempfile.TemporaryDirectory() as tmpdir:
        csv_prefix = os.path.join(tmpdir, "locust")
        env = os.environ.copy()
        env["NB_USERS"] = str(nb_users)

        cmd = [
            sys.executable, "-m", "locust",
            "-f", str(LOCUSTFILE),
            "--headless",
            "-u", str(concurrent_users),
            "-r", str(spawn_rate),
            "-t", f"{duration_s}s",
            "--host", app_url,
            "--csv", csv_prefix,
            "--reset-stats",         # ignore les stats du ramp-up
            "--only-summary",
        ]

        print(f"  [Locust] users={concurrent_users}, spawn_rate={spawn_rate}, "
              f"duration={duration_s}s")
        try:
            subprocess.run(
                cmd, env=env, timeout=duration_s + 120,
                check=False  # on gère les erreurs nous-mêmes
            )
        except subprocess.TimeoutExpired:
            print("  [WARN] Locust a dépassé le timeout")

        return _parse_locust_csv(csv_prefix + "_stats.csv")


def _parse_locust_csv(stats_file: str) -> tuple[float, int]:
    if not os.path.exists(stats_file):
        print(f"  [WARN] Fichier CSV locust introuvable: {stats_file}")
        return 0.0, -1

    with open(stats_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if "Aggregated" in row.get("Name", ""):
                avg_ms = float(row.get("Average Response Time", 0) or 0)
                failed = int(row.get("Failure Count", 0) or 0)
                return avg_ms, failed

    print("  [WARN] Ligne 'Aggregated' introuvable dans le CSV Locust")
    return 0.0, -1


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n[CSV] Écrit: {path}")


# ──────────────────────────────────────────────────────────────────────────────
# Expériences
# ──────────────────────────────────────────────────────────────────────────────

def experiment_conc(app_url: str, duration_s: int, skip_seed: bool, no_kill: bool, nb_runs: int) -> None:
    """
    Données fixes: 1000 users, 50 posts/user, 20 follows.
    Varie: utilisateurs simultanés [1, 10, 20, 50, 100, 1000].
    """
    print("\n" + "="*60)
    print("EXPÉRIENCE 1 – Passage à l'échelle sur la CHARGE")
    print("="*60)

    if not skip_seed:
        seed(posts_per_user=50, follows=20, clear=True)

    rows: list[dict] = []

    for param in CONC_PARAMS:
        for run in range(1, nb_runs + 1):
            print(f"\n[CONC] PARAM={param}, run={run}/{nb_runs}")
            if not no_kill:
                kill_all_instances()
            nb_instances = get_instance_count()
            avg_ms, failed = run_locust(app_url, param, duration_s)
            row = {
                "PARAM": param,
                "AVG_TIME": f"{round(avg_ms)}ms",
                "RUN": run,
                "FAILED": 1 if failed > 0 else 0,
                "NB_INSTANCES": nb_instances,
            }
            rows.append(row)
            print(f"  → AVG={round(avg_ms)}ms | FAILED={failed} | instances={nb_instances}")
            time.sleep(5)

    write_csv(OUT_DIR / "conc.csv",
              ["PARAM", "AVG_TIME", "RUN", "FAILED", "NB_INSTANCES"],
              rows)


def experiment_fanout(app_url: str, duration_s: int, skip_seed: bool, no_kill: bool, nb_runs: int) -> None:
    """
    Données: 1000 users, 100 posts/user, 50 users simultanés.
    Varie: followees [20, 40, 60].
    """
    print("\n" + "="*60)
    print("EXPÉRIENCE 2 – Passage à l'échelle sur la TAILLE DES DONNÉES")
    print("="*60)

    rows: list[dict] = []

    for param in FANOUT_PARAMS:
        print(f"\n[FANOUT] followees={param}")
        if not skip_seed:
            seed(posts_per_user=100, follows=param, clear=True)

        for run in range(1, nb_runs + 1):
            print(f"\n[FANOUT] PARAM={param}, run={run}/{nb_runs}")
            if not no_kill:
                kill_all_instances()
            nb_instances = get_instance_count()
            avg_ms, failed = run_locust(app_url, concurrent_users=50, duration_s=duration_s)
            row = {
                "PARAM": param,
                "AVG_TIME": f"{round(avg_ms)}ms",
                "RUN": run,
                "FAILED": 1 if failed > 0 else 0,
                "NB_INSTANCES": nb_instances,
            }
            rows.append(row)
            print(f"  → AVG={round(avg_ms)}ms | FAILED={failed} | instances={nb_instances}")
            time.sleep(5)

    write_csv(OUT_DIR / "fanout.csv",
              ["PARAM", "AVG_TIME", "RUN", "FAILED", "NB_INSTANCES"],
              rows)


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark TinyInsta")
    parser.add_argument("--app-url", required=True,
                        help="URL de l'appli déployée, ex: https://mon-projet.uc.r.appspot.com")
    parser.add_argument("--duration", type=int, default=60,
                        help="Durée de chaque test Locust en secondes (défaut: 60)")
    parser.add_argument("--skip-seed", action="store_true",
                        help="Ne pas re-seeder avant les tests")
    parser.add_argument("--runs", type=int, default=3,
                        help="Nombre de runs par configuration (défaut: 3)")
    parser.add_argument("--no-kill", action="store_true",
                        help="Ne pas tuer les instances entre les runs (désactive le cold start garanti)")
    parser.add_argument("--only-conc", action="store_true")
    parser.add_argument("--only-fanout", action="store_true")
    args = parser.parse_args()

    app_url = args.app_url.rstrip("/")

    if args.only_fanout:
        experiment_fanout(app_url, args.duration, args.skip_seed, args.no_kill, args.runs)
    elif args.only_conc:
        experiment_conc(app_url, args.duration, args.skip_seed, args.no_kill, args.runs)
    else:
        experiment_conc(app_url, args.duration, args.skip_seed, args.no_kill, args.runs)
        experiment_fanout(app_url, args.duration, args.skip_seed, args.no_kill, args.runs)

    print("\n[DONE] Benchmarks terminés. Résultats dans out/")


if __name__ == "__main__":
    main()
