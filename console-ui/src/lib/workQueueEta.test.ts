import { describe, expect, it } from 'vitest';
import type { SourceWorkQueueItem } from './api';
import {
  computeWorkQueueEta,
  formatDurationSec,
  medianSec,
  sampleFromItem,
  weightedAverageSec,
  type DurationSample,
} from './workQueueEta';

function item(
  partial: Partial<SourceWorkQueueItem> &
    Pick<SourceWorkQueueItem, 'id' | 'status'>,
): SourceWorkQueueItem {
  return {
    sha256: 'a'.repeat(64),
    translate: {
      wanted: true,
      force: false,
      status: 'done',
    },
    summarize: {
      wanted: true,
      force: false,
      status: 'done',
    },
    created_at: 1000,
    notify: true,
    notify_sent: true,
    ...partial,
  };
}

describe('sampleFromItem', () => {
  it('accepts done items with start/finish', () => {
    const s = sampleFromItem(
      item({
        id: '1',
        status: 'done',
        started_at: 1000,
        finished_at: 1180,
      }),
    );
    expect(s).toEqual({ finishedAt: 1180, durationSec: 180 });
  });

  it('rejects too-short or missing stamps', () => {
    expect(
      sampleFromItem(
        item({ id: '1', status: 'done', started_at: 1000, finished_at: 1003 }),
      ),
    ).toBeNull();
    expect(
      sampleFromItem(item({ id: '1', status: 'done', finished_at: 1100 })),
    ).toBeNull();
    expect(
      sampleFromItem(item({ id: '1', status: 'queued', started_at: 1, finished_at: 100 })),
    ).toBeNull();
  });
});

describe('weightedAverageSec / median', () => {
  it('weights recent samples more heavily', () => {
    const now = 10_000;
    const samples: DurationSample[] = [
      { finishedAt: now - 10, durationSec: 100 }, // very recent
      { finishedAt: now - 3600, durationSec: 1000 }, // 1h ago
    ];
    const avg = weightedAverageSec(samples, now);
    // closer to 100 than to 1000
    expect(avg).toBeLessThan(400);
    expect(avg).toBeGreaterThan(100);
  });

  it('median is middle value', () => {
    const samples: DurationSample[] = [
      { finishedAt: 1, durationSec: 10 },
      { finishedAt: 2, durationSec: 30 },
      { finishedAt: 3, durationSec: 50 },
    ];
    expect(medianSec(samples)).toBe(30);
  });
});

describe('computeWorkQueueEta', () => {
  const now = 1_000_000;

  it('returns unreliable with fewer than 2 samples', () => {
    const eta = computeWorkQueueEta(
      [
        item({
          id: 'a',
          status: 'done',
          started_at: now - 200,
          finished_at: now - 100,
          title: 'Only one',
        }),
        item({ id: 'q', status: 'queued', created_at: now }),
      ],
      now,
    );
    expect(eta.reliable).toBe(false);
    expect(eta.etaSec).toBeNull();
    expect(eta.sampleCount).toBe(1);
    expect(eta.lastTitle).toBe('Only one');
  });

  it('estimates remaining time from weighted avg × remaining units', () => {
    const items: SourceWorkQueueItem[] = [
      item({
        id: 'd1',
        status: 'done',
        started_at: now - 600,
        finished_at: now - 300, // 300s
        title: 'Old',
      }),
      item({
        id: 'd2',
        status: 'done',
        started_at: now - 200,
        finished_at: now - 50, // 150s more recent
        title: 'Recent finish',
      }),
      item({
        id: 'r1',
        status: 'running',
        started_at: now - 60,
        created_at: now - 60,
        title: 'In flight',
        translate: { wanted: true, force: false, status: 'running' },
        summarize: { wanted: false, force: false, status: 'skipped' },
      }),
      item({ id: 'q1', status: 'queued', created_at: now }),
      item({ id: 'q2', status: 'queued', created_at: now }),
    ];
    const eta = computeWorkQueueEta(items, now);
    expect(eta.reliable).toBe(true);
    expect(eta.sampleCount).toBe(2);
    expect(eta.lastFinishedAt).toBe(now - 50);
    expect(eta.lastDurationSec).toBe(150);
    expect(eta.lastTitle).toBe('Recent finish');
    expect(eta.runningElapsedSec).toBe(60);
    // remaining: 2 queued + fractional running
    expect(eta.remainingUnits).toBeGreaterThan(2);
    expect(eta.remainingUnits).toBeLessThan(3.5);
    expect(eta.etaSec).not.toBeNull();
    expect(eta.etaSec!).toBeGreaterThan(0);
    expect(eta.etaAt).toBe(now + Math.round(eta.etaSec!));
  });

  it('counts throughput windows', () => {
    const items: SourceWorkQueueItem[] = [];
    for (let i = 0; i < 5; i++) {
      items.push(
        item({
          id: `h${i}`,
          status: 'done',
          started_at: now - 800 - i * 10,
          finished_at: now - 600 - i * 10, // all within last hour
        }),
      );
    }
    // one older than 1h
    items.push(
      item({
        id: 'old',
        status: 'done',
        started_at: now - 5000,
        finished_at: now - 4800,
      }),
    );
    const eta = computeWorkQueueEta(items, now);
    expect(eta.doneLastHour).toBe(5);
  });

  it('eta 0 when queue empty and reliable', () => {
    const eta = computeWorkQueueEta(
      [
        item({
          id: 'd1',
          status: 'done',
          started_at: now - 400,
          finished_at: now - 200,
        }),
        item({
          id: 'd2',
          status: 'done',
          started_at: now - 180,
          finished_at: now - 20,
        }),
      ],
      now,
    );
    expect(eta.reliable).toBe(true);
    expect(eta.remainingUnits).toBe(0);
    expect(eta.etaSec).toBe(0);
  });
});

describe('formatDurationSec', () => {
  it('formats common ranges', () => {
    expect(formatDurationSec(12)).toBe('12s');
    expect(formatDurationSec(65)).toBe('1m 5s');
    expect(formatDurationSec(3600)).toBe('1h');
    expect(formatDurationSec(3700)).toBe('1h 1m');
    expect(formatDurationSec(90_000)).toBe('1d 1h');
  });
});
