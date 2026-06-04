"""Desktop DB access layer for Qt admin UI.

Reuses project's common/ for normalize/extracted helpers.
Similar to web/db_util.py + crawler/db.py but Qt-friendly (no web deps).

Usage:
    from desktop.db import DesktopDB
    db = DesktopDB("postgresql://...")
    rows = db.fetch_messages(limit=50)
"""

import os
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.extensions import connection as PsycopgConnection

# Project common (from root)
from common.normalize import normalize_code, normalize_code_key
from common.extracted import (
    has_meaningful_extracted,
    parse_int,
    parse_float,
    parse_bool,
    EXTRACTED_PROFILE_KEYS,
)


class DesktopDB:
    def __init__(self, database_url: Optional[str] = None):
        self.database_url = database_url or os.getenv(
            "DATABASE_URL", "postgresql://tguser:tgpwd@localhost:5433/tg_crawler"
        )
        self.conn: Optional[PsycopgConnection] = None
        self._connect()

    def _connect(self):
        if self.conn and not self.conn.closed:
            return
        self.conn = psycopg2.connect(self.database_url, cursor_factory=RealDictCursor)
        self.conn.autocommit = False

    def close(self):
        if self.conn and not self.conn.closed:
            self.conn.close()

    def fetch_messages(
        self,
        status: Optional[str] = None,
        keyword: Optional[str] = None,
        limit: int = 100,
        owner_user_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Basic messages fetch (simplified version of web list query)."""
        conditions = ["1=1"]
        params: Dict[str, Any] = {}

        if status:
            conditions.append("m.review_status = %(status)s")
            params["status"] = status
        if keyword:
            conditions.append(
                "(m.text_content ILIKE %(kw)s OR m.extracted_json::text ILIKE %(kw)s)"
            )
            params["kw"] = f"%{keyword}%"
        if owner_user_id is not None:
            conditions.append("m.owner_user_id = %(owner)s")
            params["owner"] = owner_user_id

        where = " AND ".join(conditions)

        sql = f"""
            SELECT
                m.id, m.telegram_message_id, m.telegram_date,
                m.text_content, m.review_status, m.is_flagged,
                m.has_media, m.extract_confidence, m.extracted_json,
                m.manual_tags,
                COALESCE(p.display_nickname, m.extracted_json->>'nickname') AS nickname,
                COALESCE(p.internal_code, m.extracted_json->>'code') AS code,
                COALESCE(p.province, m.extracted_json->>'province') AS province,
                c.username AS channel_name,
                (SELECT COUNT(*) FROM media_files WHERE message_id = m.id) AS media_count
            FROM messages m
            LEFT JOIN profiles p ON p.message_id = m.id
            LEFT JOIN channels c ON c.id = m.channel_id
            WHERE {where}
            ORDER BY m.telegram_date DESC, m.id DESC
            LIMIT %(limit)s
        """
        params["limit"] = limit

        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def update_review(
        self,
        msg_id: int,
        review_status: str,
        review_notes: Optional[str] = None,
        is_flagged: bool = False,
        manual_tags: Optional[List[str]] = None,
        reviewer_id: Optional[int] = None,
    ) -> bool:
        """Update review status + audit (simplified)."""
        old = None
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT review_status, is_flagged, review_notes, manual_tags FROM messages WHERE id = %s",
                (msg_id,),
            )
            old = cur.fetchone()

            cur.execute(
                """
                UPDATE messages
                SET review_status = %s,
                    review_notes = %s,
                    is_flagged = %s,
                    manual_tags = %s,
                    reviewer_id = %s,
                    review_time = NOW()
                WHERE id = %s
                """,
                (review_status, review_notes, is_flagged, manual_tags, reviewer_id, msg_id),
            )

            # Simple audit
            cur.execute(
                """
                INSERT INTO audit_logs (message_id, reviewer_id, action, old_values, new_values)
                VALUES (%s, %s, 'review', %s, %s)
                """,
                (
                    msg_id,
                    reviewer_id,
                    psycopg2.extras.Json(dict(old)) if old else None,
                    psycopg2.extras.Json(
                        {"status": review_status, "flagged": is_flagged, "notes": review_notes, "tags": manual_tags}
                    ),
                ),
            )
            self.conn.commit()
        return True

    def fetch_persons(self, limit: int = 50, keyword: Optional[str] = None) -> List[Dict[str, Any]]:
        """Simplified persons fetch (code or album grouping)."""
        # For demo, just recent profiles with code
        sql = """
            SELECT p.*, m.telegram_date, c.username as channel_name
            FROM profiles p
            LEFT JOIN messages m ON m.id = p.message_id
            LEFT JOIN channels c ON c.id = m.channel_id
            WHERE (%(kw)s IS NULL OR p.display_nickname ILIKE %(kw)s OR p.internal_code ILIKE %(kw)s)
            ORDER BY p.updated_at DESC NULLS LAST, p.id DESC
            LIMIT %(limit)s
        """
        params = {"limit": limit, "kw": f"%{keyword}%" if keyword else None}
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def get_runtime_stats(self) -> Dict[str, Any]:
        """Quick stats for dashboard/ops."""
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) as total_messages,
                    COUNT(*) FILTER (WHERE review_status='pending') as pending,
                    COUNT(*) FILTER (WHERE review_status='approved') as approved,
                    COUNT(*) FILTER (WHERE has_media) as with_media
                FROM messages
            """)
            return dict(cur.fetchone() or {})

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()
