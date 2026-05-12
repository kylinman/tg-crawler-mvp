import argparse
from pathlib import Path

import psycopg2
from psycopg2 import sql


def connect(dbname: str, user: str, host: str, port: int, password: str):
    kwargs = {
        'dbname': dbname,
        'user': user,
        'host': host,
        'port': port,
    }
    if password:
        kwargs['password'] = password
    return psycopg2.connect(**kwargs)


def ensure_role_and_db(args):
    admin_conn = connect('postgres', args.admin_user, args.host, args.port, args.admin_password)
    admin_conn.autocommit = True
    try:
        with admin_conn.cursor() as cur:
            cur.execute('SELECT 1 FROM pg_roles WHERE rolname = %s', (args.app_user,))
            role_exists = cur.fetchone() is not None

            if not role_exists:
                cur.execute(
                    sql.SQL('CREATE ROLE {} LOGIN PASSWORD %s').format(sql.Identifier(args.app_user)),
                    (args.app_password,),
                )
            else:
                cur.execute(
                    sql.SQL('ALTER ROLE {} WITH LOGIN PASSWORD %s').format(sql.Identifier(args.app_user)),
                    (args.app_password,),
                )

            cur.execute('SELECT 1 FROM pg_database WHERE datname = %s', (args.app_db,))
            db_exists = cur.fetchone() is not None
            if not db_exists:
                cur.execute(
                    sql.SQL('CREATE DATABASE {} OWNER {}').format(
                        sql.Identifier(args.app_db),
                        sql.Identifier(args.app_user),
                    )
                )

            cur.execute(
                sql.SQL('GRANT ALL PRIVILEGES ON DATABASE {} TO {}').format(
                    sql.Identifier(args.app_db),
                    sql.Identifier(args.app_user),
                )
            )
    finally:
        admin_conn.close()


def run_init_sql(args):
    init_sql_path = Path(args.init_sql)
    if not init_sql_path.exists():
        raise FileNotFoundError(f'init.sql not found: {init_sql_path}')

    content = init_sql_path.read_text(encoding='utf-8')
    app_conn = connect(args.app_db, args.app_user, args.host, args.port, args.app_password)
    app_conn.autocommit = False
    try:
        with app_conn.cursor() as cur:
            cur.execute(content)
        app_conn.commit()
    finally:
        app_conn.close()


def parse_args():
    parser = argparse.ArgumentParser(description='Initialize tg-crawler local PostgreSQL database')
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--admin-user', required=True)
    parser.add_argument('--admin-password', default='')
    parser.add_argument('--app-db', required=True)
    parser.add_argument('--app-user', required=True)
    parser.add_argument('--app-password', required=True)
    parser.add_argument('--init-sql', required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    ensure_role_and_db(args)
    run_init_sql(args)
    print(f'Database initialized successfully: {args.app_db}')


if __name__ == '__main__':
    main()
