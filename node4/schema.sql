-- Node 4 세션 DB
-- 로컬 검증은 SQLite, AWS로 옮길 때는 같은 컬럼을 RDS(MySQL)에 만든다.
-- 제안서 6.5: 세션 ID, 파일 경로, 생성 시각, 프레임 색상, 레이아웃, 컷별 smile_score

CREATE TABLE IF NOT EXISTS shots (
    session_id   TEXT NOT NULL,
    shot          INTEGER NOT NULL,
    angle         TEXT NOT NULL,          -- front | side
    node          TEXT,
    filename      TEXT,
    file_path     TEXT,
    face_count    INTEGER,
    smile_score   REAL,
    gaze_ok       INTEGER,
    faces_json    TEXT,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (session_id, shot, angle)
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    layout        TEXT,                   -- vertical | wide
    frame_color   TEXT,                   -- white | pink | blue | black
    face_count    INTEGER,
    best_shot     TEXT,
    shot_scores   TEXT,                   -- JSON: 컷별 smile_score
    file_path     TEXT,
    download_url  TEXT,
    s3_url        TEXT,
    storage       TEXT,                   -- local | s3
    status        TEXT NOT NULL,          -- OK | ERROR
    error         TEXT,
    created_at    TEXT NOT NULL
);
