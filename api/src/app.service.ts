import { Injectable, NotFoundException } from '@nestjs/common';
import * as fs from 'fs/promises';
import * as path from 'path';

@Injectable()
export class AppService {
  private readonly recordingsDir = process.env.RECORDINGS_DIR || '/recordings';

  async getCameras(): Promise<string[]> {
    try {
      const entries = await fs.readdir(this.recordingsDir, { withFileTypes: true });
      return entries
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);
    } catch (error) {
      console.error('Error listing cameras:', error);
      return [];
    }
  }

  async getEvents(camera: string): Promise<any[]> {
    const cameraDir = path.join(this.recordingsDir, camera);
    try {
      // Check if camera dir exists
      await fs.access(cameraDir);

      // List dates
      const dates = await fs.readdir(cameraDir, { withFileTypes: true });
      const events = [];

      for (const dateEntry of dates) {
        if (!dateEntry.isDirectory()) continue;
        const datePath = path.join(cameraDir, dateEntry.name);
        
        try {
          const timeEntries = await fs.readdir(datePath, { withFileTypes: true });
          for (const timeEntry of timeEntries) {
            if (timeEntry.isDirectory()) {
               events.push({
                 id: `${dateEntry.name}/${timeEntry.name}`,
                 date: dateEntry.name,
                 timestamp: timeEntry.name,
                 path: path.join(cameraDir, dateEntry.name, timeEntry.name)
               });
            }
          }
        } catch (e) {
          console.warn(`Could not read date directory ${dateEntry.name}`, e);
        }
      }
      
      // Sort events by timestamp (descending usually better)
      return events.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
    } catch (error) {
      if (error.code === 'ENOENT') {
        throw new NotFoundException(`Camera ${camera} not found`);
      }
      throw error;
    }
  }

  async generatePlaylist(camera: string, eventId: string): Promise<string> {
    // eventId is expected to be "YYYYMMDD/YYYYMMDD_HHMMSS"
    // Prevent directory traversal
    if (eventId.includes('..') || camera.includes('..')) {
      throw new NotFoundException('Invalid path');
    }

    const eventDir = path.join(this.recordingsDir, camera, eventId);
    
    try {
      const files = await fs.readdir(eventDir);
      const tsFiles = files
        .filter(f => f.endsWith('.ts'))
        .sort(); // Sort alphabetically (names usually have sequence numbers)

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
        // The URL for the segment. Relative URL works if served from same base.
        // We will serve segments from :event_id/segment.ts
        m3u8Content += `${file}\n`;
      }

      m3u8Content += '#EXT-X-ENDLIST\n';
      return m3u8Content;
    } catch (error) {
       if (error.code === 'ENOENT') {
        throw new NotFoundException(`Event not found`);
      }
      throw error;
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

