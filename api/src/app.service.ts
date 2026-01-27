import { Injectable, NotFoundException } from '@nestjs/common';
import { access } from 'fs/promises';
import { join } from 'path';
import { DatabaseService } from './database.service';

export interface Stream {
  id: number;
  stream_id: string;
  name: string | null;
  source_type: string | null;
  source_url: string | null;
  ready: boolean;
  last_seen_at: Date | null;
}

export interface Analysis {
  id: number;
  description: string | null;
  danger: boolean;
  danger_level: number;
  danger_details: string | null;
  created_at: Date;
}

export interface Recording {
  id: number;
  stream_id: string;
  filename: string;
  filepath: string;
  recorded_at: Date;
  analysis: Analysis | null;
}

export interface PaginatedResult<T> {
  data: T[];
  total: number;
  page: number;
  limit: number;
}

export interface DetectorConfig {
  stream_id: string;
  enabled: boolean;
  crop_x1: number;
  crop_y1: number;
  crop_x2: number;
  crop_y2: number;
  sensitivity: number;
}

export interface UpdateDetectorConfigDto {
  enabled?: boolean;
  crop_x1?: number;
  crop_y1?: number;
  crop_x2?: number;
  crop_y2?: number;
  sensitivity?: number;
}

@Injectable()
export class AppService {
  private readonly recordingsDir = process.env.RECORDINGS_DIR || '/recordings';

  constructor(private readonly db: DatabaseService) {}

  async getStreams(): Promise<Stream[]> {
    const { rows } = await this.db.query<Stream>(
      `SELECT id, stream_id, name, source_type, source_url, ready, last_seen_at
       FROM streams ORDER BY name NULLS LAST, stream_id`
    );
    return rows;
  }

  async getRecordings(
    streamId: string,
    page = 1,
    limit = 50,
  ): Promise<PaginatedResult<Recording>> {
    const offset = (page - 1) * limit;

    interface RecordingRow {
      id: number;
      stream_id: string;
      filename: string;
      filepath: string;
      recorded_at: Date;
      analysis_id: number | null;
      analysis_description: string | null;
      analysis_danger: boolean | null;
      analysis_danger_level: number | null;
      analysis_danger_details: string | null;
      analysis_created_at: Date | null;
    }

    const [recordings, countResult] = await Promise.all([
      this.db.query<RecordingRow>(
        `SELECT 
           r.id, r.stream_id, r.filename, r.filepath, r.recorded_at,
           a.id as analysis_id,
           a.description as analysis_description,
           a.danger as analysis_danger,
           a.danger_level as analysis_danger_level,
           a.danger_details as analysis_danger_details,
           a.created_at as analysis_created_at
         FROM recordings r
         LEFT JOIN analysis a ON r.id = a.recording_id
         WHERE r.stream_id = $1
         ORDER BY r.recorded_at DESC
         LIMIT $2 OFFSET $3`,
        [streamId, limit, offset]
      ),
      this.db.query<{ count: string }>(
        `SELECT COUNT(*) as count FROM recordings WHERE stream_id = $1`,
        [streamId]
      ),
    ]);

    const data: Recording[] = recordings.rows.map((row) => ({
      id: row.id,
      stream_id: row.stream_id,
      filename: row.filename,
      filepath: row.filepath,
      recorded_at: row.recorded_at,
      analysis: row.analysis_id
        ? {
            id: row.analysis_id,
            description: row.analysis_description,
            danger: row.analysis_danger ?? false,
            danger_level: row.analysis_danger_level ?? 0,
            danger_details: row.analysis_danger_details,
            created_at: row.analysis_created_at!,
          }
        : null,
    }));

    return {
      data,
      total: parseInt(countResult.rows[0].count, 10),
      page,
      limit,
    };
  }

  async getRecordingPath(streamId: string, recordingId: number): Promise<string> {
    const { rows } = await this.db.query<Recording>(
      `SELECT filepath FROM recordings WHERE id = $1 AND stream_id = $2`,
      [recordingId, streamId]
    );

    if (!rows.length) {
      throw new NotFoundException('Recording not found');
    }

    const filePath = join(this.recordingsDir, rows[0].filepath);

    try {
      await access(filePath);
    } catch {
      throw new NotFoundException('Recording file not found');
    }

    return filePath;
  }

  async getDetectorConfig(streamId: string): Promise<DetectorConfig> {
    const { rows } = await this.db.query<DetectorConfig>(
      `SELECT stream_id, enabled, crop_x1, crop_y1, crop_x2, crop_y2, sensitivity
       FROM detector_configs
       WHERE stream_id = $1`,
      [streamId]
    );

    if (!rows.length) {
      // Return default config if none exists
      return {
        stream_id: streamId,
        enabled: true,
        crop_x1: 0,
        crop_y1: 0,
        crop_x2: 100,
        crop_y2: 100,
        sensitivity: 50,
      };
    }

    return rows[0];
  }

  async updateDetectorConfig(
    streamId: string,
    update: UpdateDetectorConfigDto,
  ): Promise<DetectorConfig> {
    // Validate crop values are within 0-100 range
    const cropFields = ['crop_x1', 'crop_y1', 'crop_x2', 'crop_y2'] as const;
    for (const field of cropFields) {
      if (update[field] !== undefined) {
        if (update[field] < 0 || update[field] > 100) {
          throw new Error(`${field} must be between 0 and 100`);
        }
      }
    }

    // Validate x2 > x1 and y2 > y1 if both are provided
    if (update.crop_x1 !== undefined && update.crop_x2 !== undefined) {
      if (update.crop_x2 <= update.crop_x1) {
        throw new Error('crop_x2 must be greater than crop_x1');
      }
    }
    if (update.crop_y1 !== undefined && update.crop_y2 !== undefined) {
      if (update.crop_y2 <= update.crop_y1) {
        throw new Error('crop_y2 must be greater than crop_y1');
      }
    }

    if (update.sensitivity !== undefined) {
      if (update.sensitivity < 0 || update.sensitivity > 100) {
        throw new Error('sensitivity must be between 0 and 100');
      }
    }

    // Upsert the config
    const { rows } = await this.db.query<DetectorConfig>(
      `INSERT INTO detector_configs (stream_id, enabled, crop_x1, crop_y1, crop_x2, crop_y2, sensitivity)
       VALUES ($1, $2, $3, $4, $5, $6, $7)
       ON CONFLICT (stream_id) DO UPDATE SET
         enabled = COALESCE($2, detector_configs.enabled),
         crop_x1 = COALESCE($3, detector_configs.crop_x1),
         crop_y1 = COALESCE($4, detector_configs.crop_y1),
         crop_x2 = COALESCE($5, detector_configs.crop_x2),
         crop_y2 = COALESCE($6, detector_configs.crop_y2),
         sensitivity = COALESCE($7, detector_configs.sensitivity),
         updated_at = CURRENT_TIMESTAMP
       RETURNING stream_id, enabled, crop_x1, crop_y1, crop_x2, crop_y2, sensitivity`,
      [
        streamId,
        update.enabled ?? true,
        update.crop_x1 ?? 0,
        update.crop_y1 ?? 0,
        update.crop_x2 ?? 100,
        update.crop_y2 ?? 100,
        update.sensitivity ?? 50,
      ]
    );

    return rows[0];
  }
}
