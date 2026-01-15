import { Controller, Get, Param, Res, StreamableFile } from '@nestjs/common';
import { AppService } from './app.service';
import { Response } from 'express';
import { createReadStream } from 'fs';

@Controller('cameras')
export class AppController {
  constructor(private readonly appService: AppService) {}

  @Get()
  async getCameras() {
    return this.appService.getCameras();
  }

  @Get(':camera/events')
  async getEvents(@Param('camera') camera: string) {
    return this.appService.getEvents(camera);
  }

  @Get(':camera/events/:date/:timestamp/playlist.m3u8')
  async getPlaylist(
    @Param('camera') camera: string,
    @Param('date') date: string,
    @Param('timestamp') timestamp: string,
    @Res() res: Response,
  ) {
    const eventId = `${date}/${timestamp}`;
    const playlist = await this.appService.generatePlaylist(camera, eventId);
    res.set({
      'Content-Type': 'application/vnd.apple.mpegurl',
      'Content-Disposition': 'inline; filename="playlist.m3u8"',
    });
    res.send(playlist);
  }

  @Get(':camera/events/:date/:timestamp/:segment')
  async getSegment(
    @Param('camera') camera: string,
    @Param('date') date: string,
    @Param('timestamp') timestamp: string,
    @Param('segment') segment: string,
    @Res({ passthrough: true }) res: Response,
  ) {
    const eventId = `${date}/${timestamp}`;
    const filePath = await this.appService.getSegmentPath(camera, eventId, segment);
    
    const file = createReadStream(filePath);
    res.set({
      'Content-Type': 'video/mp2t',
      'Content-Disposition': `inline; filename="${segment}"`,
    });
    return new StreamableFile(file);
  }
}

