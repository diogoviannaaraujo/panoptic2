import {
  Controller,
  Get,
  Param,
  ParseIntPipe,
  Query,
  Res,
  StreamableFile,
} from '@nestjs/common';
import { Response } from 'express';
import { createReadStream } from 'fs';
import { AppService, Camera, PaginatedResult, Recording } from './app.service';

@Controller('cameras')
export class AppController {
  constructor(private readonly appService: AppService) {}

  @Get()
  getCameras(): Promise<Camera[]> {
    return this.appService.getCameras();
  }

  @Get(':streamId/recordings')
  getRecordings(
    @Param('streamId') streamId: string,
    @Query('page', new ParseIntPipe({ optional: true })) page = 1,
    @Query('limit', new ParseIntPipe({ optional: true })) limit = 50,
  ): Promise<PaginatedResult<Recording>> {
    return this.appService.getRecordings(streamId, page, limit);
  }

  @Get(':streamId/recordings/:id')
  async getRecording(
    @Param('streamId') streamId: string,
    @Param('id', ParseIntPipe) id: number,
    @Res({ passthrough: true }) res: Response,
  ): Promise<StreamableFile> {
    const filePath = await this.appService.getRecordingPath(streamId, id);

    res.set({
      'Content-Type': 'video/mp2t',
      'Content-Disposition': 'inline',
    });

    return new StreamableFile(createReadStream(filePath));
  }
}
