"""
Seeder rapide pour TinyInsta avec batch writes Datastore.
Supporte le remplacement exact des follows (pas de merge).

Usage:
    python benchmark/seed_fast.py --users 1000 --posts-per-user 50 --follows 20
    python benchmark/seed_fast.py --users 1000 --posts-per-user 100 --follows 40 --clear
"""
from __future__ import annotations
import argparse
import random
import sys
from datetime import datetime, timedelta
from google.cloud import datastore

BATCH_SIZE = 500  # max Datastore batch size


def clear_kind(client: datastore.Client, kind: str) -> int:
    """Supprime toutes les entités d'un kind, par pages de BATCH_SIZE pour éviter les timeouts gRPC."""
    print(f"  Suppression de toutes les entités '{kind}'...")
    total = 0
    while True:
        query = client.query(kind=kind)
        query.keys_only()
        # Fetch en petits lots pour rester sous le timeout gRPC de 60s
        keys = [e.key for e in query.fetch(limit=BATCH_SIZE)]
        if not keys:
            break
        client.delete_multi(keys)
        total += len(keys)
        print(f"  Supprimé {total}...", end="\r")
    print(f"\n  {total} entités '{kind}' supprimées.")
    return total


def seed(users: int, posts_per_user: int, follows: int,
         prefix: str = "user", clear: bool = False) -> dict:
    client = datastore.Client()

    if clear:
        print("[Seed] Nettoyage des données existantes...")
        clear_kind(client, "Post")
        clear_kind(client, "User")

    user_names = [f"{prefix}{i}" for i in range(1, users + 1)]
    others_map = {n: [u for u in user_names if u != n] for n in user_names}

    # ── 1. Créer les utilisateurs ──────────────────────────────────────────
    print(f"[Seed] Création/mise à jour de {users} utilisateurs (follows={follows})...")
    batch: list[datastore.Entity] = []
    nb_follows = min(follows, users - 1)

    for name in user_names:
        key = client.key("User", name)
        entity = datastore.Entity(key)
        entity["follows"] = random.sample(others_map[name], nb_follows)
        batch.append(entity)
        if len(batch) == BATCH_SIZE:
            client.put_multi(batch)
            batch = []
    if batch:
        client.put_multi(batch)
    print(f"[Seed] {users} utilisateurs OK.")

    # ── 2. Créer les posts ────────────────────────────────────────────────
    total_posts = users * posts_per_user
    print(f"[Seed] Création de {total_posts} posts ({posts_per_user}/user)...")
    batch = []
    base_time = datetime.utcnow()
    created = 0

    for i in range(total_posts):
        author = user_names[i % users]
        post = datastore.Entity(client.key("Post"))
        post["author"] = author
        post["content"] = f"Post {i+1} by {author}"
        post["created"] = base_time - timedelta(seconds=i)
        batch.append(post)
        if len(batch) == BATCH_SIZE:
            client.put_multi(batch)
            created += len(batch)
            print(f"  {created}/{total_posts}", end="\r")
            batch = []
    if batch:
        client.put_multi(batch)
        created += len(batch)
    print(f"\n[Seed] {created} posts OK.")

    return {"users": users, "posts": created, "follows": nb_follows}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--users", type=int, default=1000)
    p.add_argument("--posts-per-user", type=int, default=50)
    p.add_argument("--follows", type=int, default=20)
    p.add_argument("--prefix", type=str, default="user")
    p.add_argument("--clear", action="store_true",
                   help="Supprimer toutes les entités avant de seeder")
    args = p.parse_args()

    result = seed(
        users=args.users,
        posts_per_user=args.posts_per_user,
        follows=args.follows,
        prefix=args.prefix,
        clear=args.clear,
    )
    print(f"[Seed] Terminé: {result}")


if __name__ == "__main__":
    main()
