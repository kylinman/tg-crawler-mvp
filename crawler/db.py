import psycopg2
from psycopg2.extras import RealDictCursor, Json, execute_values
from config import Config

# Re-export shared utilities so existing imports from db continue to work
# during transition. New code should import directly from common.
from common.extracted import (
    EXTRACTED_PROFILE_KEYS,
    has_meaningful_extracted,
    parse_int as _to_int,
    parse_float as _to_float,
    parse_bool as _to_bool,
    is_empty_value as _is_empty_value,
)
from common.normalize import normalize_code as _normalize_code

# Keep the public name for backward compat with crawler/main.py imports
__all__ = ["Database", "has_meaningful_extracted", "EXTRACTED_PROFILE_KEYS"]

class Database:
    def __init__(self):
        self.conn = psycopg2.connect(Config.DATABASE_URL)
        self.conn.autocommit = False

    def execute(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params or {})
            return cur

    def fetchone(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.fetchone()

    def fetchall(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.fetchall()

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def try_acquire_lock(self, lock_key: int) -> bool:
        row = self.fetchone('SELECT pg_try_advisory_lock(%s)', (lock_key,))
        return bool(row[0]) if row else False

    def release_lock(self, lock_key: int):
        self.fetchone('SELECT pg_advisory_unlock(%s)', (lock_key,))

    def start_crawl_log(self, channel_id=None, owner_user_id=None) -> int:
        row = self.fetchone(
            """
            INSERT INTO crawl_logs (channel_id, owner_user_id, run_started_at, status)
            VALUES (%s, %s, NOW(), 'running')
            RETURNING id
            """,
            (channel_id, owner_user_id),
        )
        self.commit()
        return row[0]

    def bind_crawl_log_channel(self, log_id: int, channel_id: int):
        self.execute('UPDATE crawl_logs SET channel_id = %s WHERE id = %s', (channel_id, log_id))
        self.commit()

    def finish_crawl_log(self, log_id: int, status: str, processed: int, new_count: int, errors_count: int, error_details=None):
        sql = """
            UPDATE crawl_logs
            SET run_ended_at = NOW(),
                messages_processed = %s,
                messages_new = %s,
                errors_count = %s,
                error_details = %s,
                status = %s
            WHERE id = %s
        """
        self.execute(sql, (processed, new_count, errors_count, Json(error_details) if error_details else None, status, log_id))
        self.commit()

    def cleanup_stale_running_logs(self, max_age_minutes: int = 30) -> int:
        sql = """
            UPDATE crawl_logs
            SET run_ended_at = NOW(),
                status = 'failed',
                error_details = COALESCE(error_details, '[]'::jsonb) || to_jsonb(%s::text)
            WHERE status = 'running'
              AND run_ended_at IS NULL
              AND run_started_at < NOW() - (%s::text || ' minutes')::interval
            RETURNING id
        """
        marker = 'Marked failed by startup cleanup: previous run did not exit cleanly'
        rows = self.fetchall(sql, (marker, str(max_age_minutes)))
        self.commit()
        return len(rows or [])

    def insert_batch_messages(self, batch):
        if not batch:
            return
        sql = """
            INSERT INTO messages 
            (owner_user_id, channel_id, telegram_message_id, telegram_date, text_content, raw_json, 
             has_media, media_group_id, extracted_json, extract_confidence, status)
            VALUES %s
            ON CONFLICT (channel_id, telegram_message_id) DO NOTHING
        """
        values = [(
            b.get('owner_user_id'), b['channel_id'], b['telegram_message_id'], b['telegram_date'],
            b['text_content'], b['raw_json'], b['has_media'], b['media_group_id'],
            b.get('extracted_json'), b.get('extract_confidence'), b.get('status', 'pending')
        ) for b in batch]

        with self.conn.cursor() as cur:
            execute_values(cur, sql, values)
        self.conn.commit()

    def insert_media(self, message_id, file_id, media_type, s3_key, s3_url, thumb_key, thumb_url, ocr_text, file_size, owner_user_id=None):
        sql = """
            INSERT INTO media_files 
            (message_id, owner_user_id, telegram_file_id, media_type, s3_key, s3_url, thumb_key, thumb_url, ocr_text, file_size, processing_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'completed')
        """
        self.execute(sql, (message_id, owner_user_id, file_id, media_type, s3_key, s3_url, thumb_key, thumb_url, ocr_text, file_size))
        self.commit()

    def upsert_channel(self, telegram_id, username, title, description, owner_user_id=None):
        sql = """
            INSERT INTO channels (telegram_id, username, title, description, owner_user_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (username) DO UPDATE SET
                title = EXCLUDED.title,
                telegram_id = EXCLUDED.telegram_id,
                owner_user_id = COALESCE(channels.owner_user_id, EXCLUDED.owner_user_id)
            RETURNING id
        """
        row = self.fetchone(sql, (telegram_id, username, title, description, owner_user_id))
        self.commit()
        return row[0]

    def get_last_msg_id(self, username):
        row = self.fetchone(
            "SELECT last_crawled_msg_id FROM channels WHERE username = %s",
            (username,)
        )
        return row[0] if row else 0

    def update_checkpoint(self, username, last_id):
        self.execute(
            "UPDATE channels SET last_crawled_msg_id = %s WHERE username = %s",
            (last_id, username)
        )
        self.commit()

    def fetch_dedupe_candidates(self, channel_id: int, limit: int, owner_user_id=None):
        """最近入库的消息，供 LLM 判断是否与当前帖为同一人。"""
        sql = """
            SELECT id, telegram_message_id, text_content, extracted_json
            FROM messages
            WHERE channel_id = %s
              AND (%s::bigint IS NULL OR owner_user_id = %s)
            ORDER BY id DESC
            LIMIT %s
        """
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (channel_id, owner_user_id, owner_user_id, limit))
            return cur.fetchall()

    def fetch_user_crawler_settings(self, user_id: int):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    user_id,
                    tg_api_id,
                    tg_api_hash,
                    tg_phone,
                    tg_proxy_type,
                    tg_proxy_host,
                    tg_proxy_port,
                    tg_proxy_username,
                    tg_proxy_password,
                    COALESCE(target_channels, '{}'::text[]) AS target_channels
                FROM user_crawler_settings
                WHERE user_id = %s
                """,
                (user_id,),
            )
            return cur.fetchone()

    def upsert_profile_from_extracted(self, message_id: int, extracted: dict, owner_user_id=None):
        if not has_meaningful_extracted(extracted):
            return False

        tags = extracted.get('tags')
        if isinstance(tags, list):
            tags = [str(t).strip() for t in tags if str(t).strip()]
        else:
            tags = None

        contacts = extracted.get('contacts')
        if isinstance(contacts, list):
            contacts = [str(c).strip() for c in contacts if str(c).strip()]
        else:
            contacts = None

        payload = {
            'display_nickname': (extracted.get('nickname') or '').strip() or None,
            'internal_code': _normalize_code(extracted.get('code')),
            'province': (extracted.get('province') or '').strip() or None,
            'city': (extracted.get('city') or '').strip() or None,
            'age': _to_int(extracted.get('age')),
            'height': _to_int(extracted.get('height')),
            'weight': _to_int(extracted.get('weight')),
            'cup_size': (extracted.get('cup') or '').strip() or None,
            'occupation': (extracted.get('occupation') or '').strip() or None,
            'is_virgin': _to_bool(extracted.get('is_virgin')),
            'oral_available': _to_bool(extracted.get('oral')),
            'creampie_available': _to_bool(extracted.get('creampie')),
            'condomless_available': _to_bool(extracted.get('condomless')),
            'sm_available': _to_bool(extracted.get('sm')),
            'has_tattoo': _to_bool(extracted.get('tattoo')),
            'out_province_available': _to_bool(extracted.get('out_province')),
            'overnight_available': _to_bool(extracted.get('overnight')),
            'cohabitation_available': _to_bool(extracted.get('cohabitation')),
            'monthly_allowance': _to_float(extracted.get('monthly_allowance')),
            'introduction_fee': _to_float(extracted.get('intro_fee')),
            'tags': tags,
            'contact_info': {'contacts': contacts} if contacts else None,
        }

        row = self.fetchone(
            'SELECT id FROM profiles WHERE message_id = %s ORDER BY id LIMIT 1',
            (message_id,),
        )

        if row:
            sql = """
                UPDATE profiles
                SET display_nickname = %s,
                    internal_code = %s,
                    province = %s,
                    city = %s,
                    owner_user_id = COALESCE(owner_user_id, %s),
                    age = %s,
                    height = %s,
                    weight = %s,
                    cup_size = %s,
                    occupation = %s,
                    is_virgin = %s,
                    oral_available = %s,
                    creampie_available = %s,
                    condomless_available = %s,
                    sm_available = %s,
                    has_tattoo = %s,
                    out_province_available = %s,
                    overnight_available = %s,
                    cohabitation_available = %s,
                    monthly_allowance = %s,
                    introduction_fee = %s,
                    tags = %s,
                    contact_info = %s,
                    updated_at = NOW()
                WHERE id = %s
            """
            self.execute(
                sql,
                (
                    payload['display_nickname'],
                    payload['internal_code'],
                    payload['province'],
                    payload['city'],
                    owner_user_id,
                    payload['age'],
                    payload['height'],
                    payload['weight'],
                    payload['cup_size'],
                    payload['occupation'],
                    payload['is_virgin'],
                    payload['oral_available'],
                    payload['creampie_available'],
                    payload['condomless_available'],
                    payload['sm_available'],
                    payload['has_tattoo'],
                    payload['out_province_available'],
                    payload['overnight_available'],
                    payload['cohabitation_available'],
                    payload['monthly_allowance'],
                    payload['introduction_fee'],
                    payload['tags'],
                    Json(payload['contact_info']) if payload['contact_info'] else None,
                    row[0],
                ),
            )
            self.commit()
            return True

        sql = """
            INSERT INTO profiles (
                message_id,
                display_nickname,
                internal_code,
                province,
                city,
                owner_user_id,
                age,
                height,
                weight,
                cup_size,
                occupation,
                is_virgin,
                oral_available,
                creampie_available,
                condomless_available,
                sm_available,
                has_tattoo,
                out_province_available,
                overnight_available,
                cohabitation_available,
                monthly_allowance,
                introduction_fee,
                tags,
                contact_info
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s
            )
        """
        self.execute(
            sql,
            (
                message_id,
                payload['display_nickname'],
                payload['internal_code'],
                payload['province'],
                payload['city'],
                owner_user_id,
                payload['age'],
                payload['height'],
                payload['weight'],
                payload['cup_size'],
                payload['occupation'],
                payload['is_virgin'],
                payload['oral_available'],
                payload['creampie_available'],
                payload['condomless_available'],
                payload['sm_available'],
                payload['has_tattoo'],
                payload['out_province_available'],
                payload['overnight_available'],
                payload['cohabitation_available'],
                payload['monthly_allowance'],
                payload['introduction_fee'],
                payload['tags'],
                Json(payload['contact_info']) if payload['contact_info'] else None,
            ),
        )
        self.commit()
        return True
