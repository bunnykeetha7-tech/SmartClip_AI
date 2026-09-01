CREATE DATABASE IF NOT EXISTS smartclip
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE smartclip;

CREATE TABLE IF NOT EXISTS users (
  user_id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(100) NOT NULL,
  email VARCHAR(255) NOT NULL UNIQUE,
  password_hash VARCHAR(500) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS videos (
  video_id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT,
  filename VARCHAR(255) NOT NULL,
  original_url VARCHAR(1000),
  title VARCHAR(500),
  language VARCHAR(50),
  width INT,
  height INT,
  fps DOUBLE,
  duration_seconds DOUBLE,
  prompt MEDIUMTEXT,
  compression_level VARCHAR(30),
  link_type VARCHAR(50),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_videos_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS transcripts (
  transcript_id INT AUTO_INCREMENT PRIMARY KEY,
  video_id INT NOT NULL,
  transcript MEDIUMTEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_transcripts_video (video_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS transcript_segments (
  segment_id INT AUTO_INCREMENT PRIMARY KEY,
  video_id INT NOT NULL,
  text MEDIUMTEXT,
  start_time DOUBLE,
  end_time DOUBLE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_segments_video (video_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS summaries (
  summary_id INT AUTO_INCREMENT PRIMARY KEY,
  video_id INT NOT NULL,
  summary MEDIUMTEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_summaries_video (video_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS highlights (
  highlight_id INT AUTO_INCREMENT PRIMARY KEY,
  video_id INT NOT NULL,
  text MEDIUMTEXT,
  start_time DOUBLE,
  end_time DOUBLE,
  score INT,
  reason MEDIUMTEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_highlights_video (video_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
