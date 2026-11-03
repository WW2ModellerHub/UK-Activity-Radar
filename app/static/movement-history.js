(function (root) {
  'use strict';

  class MovementHistory {
    constructor(options = {}) {
      this.maxPoints = Math.max(2, Number(options.maxPoints || 120));
      this.maxAgeMs = Math.max(60_000, Number(options.maxAgeMs || 30 * 60_000));
      this.allowedTypes = new Set(options.allowedTypes || ['aircraft', 'sar']);
      this.rows = new Map();
    }

    ingest(activities, now = Date.now()) {
      for (const activity of Array.isArray(activities) ? activities : []) {
        if (!activity?.id || !this.allowedTypes.has(activity.type)) continue;
        const lat = Number(activity.lat), lon = Number(activity.lon);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
        const reported = Date.parse(activity.last_updated || '');
        const at = Number.isFinite(reported) ? reported : now;
        const points = this.rows.get(activity.id) || [];
        const last = points[points.length - 1];
        if (!last || last.lat !== lat || last.lon !== lon) {
          points.push({lat, lon, at, altitudeFt: Number.isFinite(Number(activity.altitude_ft)) ? Number(activity.altitude_ft) : null});
        }
        const cutoff = now - this.maxAgeMs;
        const fresh = points.filter(point => point.at >= cutoff).slice(-this.maxPoints);
        if (fresh.length) this.rows.set(activity.id, fresh);
      }
      const cutoff = now - this.maxAgeMs;
      for (const [id, points] of this.rows) {
        if (!points.length || points[points.length - 1].at < cutoff) this.rows.delete(id);
      }
    }

    points(id) {
      return (this.rows.get(id) || []).map(point => ({...point}));
    }

    toGeoJSON(id) {
      const points = this.points(id);
      return {
        type: 'Feature',
        properties: {activityId: id, pointCount: points.length},
        geometry: {type: 'LineString', coordinates: points.map(point => [point.lon, point.lat])}
      };
    }
  }

  root.UKActivityMovementHistory = MovementHistory;
})(typeof window !== 'undefined' ? window : globalThis);
