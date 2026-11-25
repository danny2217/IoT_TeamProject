-- Node 4 세션 DB의 RDS(MySQL) 버전.
-- 로컬 검증은 node4/schema.sql 을 SQLite에 넣고, AWS로 옮길 때 이 파일을 쓴다.
-- 컬럼 의미는 같다. 제안서 6.5: 세션 ID, 파일 경로, 생성 시각, 프레임 색상, 레이아웃, 컷별 smile_score.
-- 결과 이미지는 s3://<bucket>/results/<session_id>.jpg 이고, DB의 s3_url 에 그 주소를 적는다.

CREATE TABLE IF NOT EXISTS shots (
    session_id   VARCHAR(64)  NOT NULL,
    shot          TINYINT      NOT NULL,
    angle         VARCHAR(8)   NOT NULL,          -- front | side
    node          VARCHAR(16)  NULL,
    filename      VARCHAR(128) NULL,
    file_path     VARCHAR(512) NULL,
    face_count    INT          NULL,
    smile_score   DOUBLE       NULL,
    gaze_ok       TINYINT      NULL,
    faces_json    JSON         NULL,
    updated_at    DATETIME     NOT NULL,
    PRIMARY KEY (session_id, shot, angle)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS sessions (
    session_id    VARCHAR(64)  NOT NULL,
    layout        VARCHAR(16)  NULL,              -- vertical | wide
    frame_color   VARCHAR(16)  NULL,              -- white | pink | blue | black
    face_count    INT          NULL,
    best_shot     VARCHAR(128) NULL,
    shot_scores   JSON         NULL,              -- 컷별 smile_score
    file_path     VARCHAR(512) NULL,
    download_url  VARCHAR(512) NULL,
    s3_url        VARCHAR(512) NULL,
    storage       VARCHAR(16)  NULL,              -- local | s3
    status        VARCHAR(16)  NOT NULL,          -- OK | ERROR
    error         VARCHAR(255) NULL,
    created_at    DATETIME     NOT NULL,
    PRIMARY KEY (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
