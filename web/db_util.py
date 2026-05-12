import psycopg2.extensions


def db_execute(conn: psycopg2.extensions.connection, sql: str, params=None):
    cur = conn.cursor()
    if params is None:
        cur.execute(sql)
    else:
        cur.execute(sql, params)
    return cur
