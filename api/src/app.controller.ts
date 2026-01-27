import {
  BadRequestException,
  Body,
  Controller,
  Get,
  Param,
  ParseIntPipe,
  Put,
  Query,
  Res,
  StreamableFile,
} from '@nestjs/common';
import { Response } from 'express';
import { createReadStream } from 'fs';
import {
  AppService,
  Stream,
  PaginatedResult,
  Recording,
  DetectorConfig,
  UpdateDetectorConfigDto,
} from './app.service';

@Controller('streams')
export class AppController {
  constructor(private readonly appService: AppService) {}

  @Get()
  getStreams(): Promise<Stream[]> {
    return this.appService.getStreams();
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

  @Get(':streamId/config')
  getDetectorConfig(
    @Param('streamId') streamId: string,
  ): Promise<DetectorConfig> {
    return this.appService.getDetectorConfig(streamId);
  }

  @Put(':streamId/config')
  async updateDetectorConfig(
    @Param('streamId') streamId: string,
    @Body() body: UpdateDetectorConfigDto,
  ): Promise<DetectorConfig> {
    try {
      return await this.appService.updateDetectorConfig(streamId, body);
    } catch (error) {
      throw new BadRequestException(error.message);
    }
  }
}
