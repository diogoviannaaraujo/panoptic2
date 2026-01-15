import { Injectable, NotFoundException } from '@nestjs/common';
import * as fs from 'fs/promises';
import * as path from 'path';
import { DatabaseService } from './database.service';

interface Camera {
  id: number;
  stream_id: string;
  name: string | null;
  source_type: string | null;
  source_url: string | null;
  ready: boolean;
  bytes_received: number;
  bytes_sent: number;
  last_seen_at: Date | null;
  created_at: Date;
  updated_at: Date;
}

interface Recording {
  id: number;
  stream_id: string;
  filename: string;
  filepath: string;
  recorded_at: Date;
  created_at: Date;
}

interface EventGroup {
  id: string;
  date: string;
  timestamp: string;
  path: string;
  segments: string[];
}

@Injectable()
export class AppService {
  private readonly recordingsDir = process.env.RECORDINGS_DIR || '/recordings';

  constructor(private readonly db: DatabaseService) {}

  async getCameras(): Promise<Camera[]> {
    try {
      const result = await this.db.query<Camera>(
        'SELECT * FROM cameras ORDER BY name, stream_id'
      );
      return result.rows;
    } catch (error) {
      console.error('Error fetching cameras from database:', error);
      return [];
    }
  }

  async getEvents(camera: string): Promise<EventGroup[]> {
    try {
      // Get all recordings for this camera, grouped by directory (event)
      const result = await this.db.query<Recording>(
        `SELECT * FROM recordings 
         WHERE stream_id = $1 
         ORDER BY recorded_at DESC`,
        [camera]
      );

      if (result.rows.length === 0) {
        return [];
      }

      // Group recordings by their directory path (date/timestamp)
      const eventMap = new Map<string, EventGroup>();

      for (const recording of result.rows) {
        // filepath format is like: stream_id/YYYYMMDD/YYYYMMDD_HHMMSS/segment.ts
        // Extract the event directory (date/timestamp portion)
        const parts = recording.filepath.split('/');
        if (parts.length < 3) continue;

        // Get date and timestamp from path
        const date = parts[1]; // YYYYMMDD
        const timestamp = parts[2]; // YYYYMMDD_HHMMSS
        const eventId = `${date}/${timestamp}`;

        if (!eventMap.has(eventId)) {
          eventMap.set(eventId, {
            id: eventId,
            date,
            timestamp,
            path: path.join(this.recordingsDir, camera, date, timestamp),
            segments: [],
          });
        }

        eventMap.get(eventId)!.segments.push(recording.filename);
      }

      // Convert to array and sort by timestamp descending
      const events = Array.from(eventMap.values());
      return events.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
    } catch (error) {
      console.error('Error fetching events from database:', error);
      throw error;
    }
  }

  async generatePlaylist(camera: string, eventId: string): Promise<string> {
    // eventId is expected to be "YYYYMMDD/YYYYMMDD_HHMMSS"
    // Prevent directory traversal
    if (eventId.includes('..') || camera.includes('..')) {
      throw new NotFoundException('Invalid path');
    }

    const [date, timestamp] = eventId.split('/');
    const pathPattern = `${camera}/${date}/${timestamp}/%`;

    try {
      // Get segments for this event from database
      const result = await this.db.query<Recording>(
        `SELECT filename FROM recordings 
         WHERE stream_id = $1 AND filepath LIKE $2 
         ORDER BY filename`,
        [camera, pathPattern]
      );

      const tsFiles = result.rows
        .map(r => r.filename)
        .filter(f => f.endsWith('.ts'))
        .sort();

      if (tsFiles.length === 0) {
        throw new NotFoundException('No segments found for this event');
      }

      let m3u8Content = '#EXTM3U\n';
      m3u8Content += '#EXT-X-VERSION:3\n';
      m3u8Content += '#EXT-X-TARGETDURATION:5\n'; // Assuming 5s segments
      m3u8Content += '#EXT-X-MEDIA-SEQUENCE:0\n';
      m3u8Content += '#EXT-X-PLAYLIST-TYPE:VOD\n';

      for (const file of tsFiles) {
        // We assume 5.0 seconds per segment as per config
        m3u8Content += '#EXTINF:5.000000,\n';
        m3u8Content += `${file}\n`;
      }

      m3u8Content += '#EXT-X-ENDLIST\n';
      return m3u8Content;
    } catch (error) {
      if (error instanceof NotFoundException) {
        throw error;
      }
      console.error('Error generating playlist:', error);
      throw new NotFoundException('Event not found');
    }
  }

  async getSegmentPath(camera: string, eventId: string, segment: string): Promise<string> {
    if (eventId.includes('..') || camera.includes('..') || segment.includes('..')) {
      throw new NotFoundException('Invalid path');
    }
    const filePath = path.join(this.recordingsDir, camera, eventId, segment);
    try {
      await fs.access(filePath);
      return filePath;
    } catch {
      throw new NotFoundException('Segment not found');
    }
  }
}
