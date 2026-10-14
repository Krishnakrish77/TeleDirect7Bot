/**
 * Map view (N4) — geotagged photos on an OSM raster map with supercluster
 * marker clustering. Markers open the photo in the existing lightbox via
 * onSelect(photoId); the page itself holds no lightbox of its own.
 *
 * Tile attribution stays visible (OSM requires it) and the empty state
 * discloses that photo coordinates are sent to OSM tile servers.
 */
import { useEffect, useMemo, useState } from 'react';
import { MapContainer, Marker, Popup, TileLayer, useMap } from 'react-leaflet';
import L from 'leaflet';
import Supercluster from 'supercluster';
import 'leaflet/dist/leaflet.css';
import { fetchPhotoMapPoints } from '../api';
import type { PhotoMapPoint } from '../types';
import { photoThumbUrl } from '../api';

const OSM_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';


/** Flat list of cluster/point leaflets supercluster hands back per zoom. */
type ClusterLeaf = { cluster: true; id: number; lat: number; lon: number; count: number };
type PointLeaf = { cluster: false; photo: PhotoMapPoint };
type Leaf = ClusterLeaf | PointLeaf;

interface MapPageProps {
  /** Opens the existing lightbox at this photo id. */
  onSelect: (photoId: string) => void;
}

type PointProps = { photo: PhotoMapPoint };
type Index = Supercluster<PointProps>;

function buildLeaves(points: PhotoMapPoint[], zoom: number, index: Index): Leaf[] {
  if (!points.length) return [];
  const features = points.map((p) => ({
    type: 'Feature' as const,
    properties: { photo: p },
    geometry: { type: 'Point' as const, coordinates: [p.lon, p.lat] },
  }));
  index.load(features);
  // World-wide view: load everything at the current zoom.
  const clusters = index.getClusters([-180, -85, 180, 85], zoom);
  return clusters.map((f): Leaf => {
    const [lon, lat] = f.geometry.coordinates;
    if ('cluster' in f.properties) {
      return {
        cluster: true,
        id: f.id as number,
        lat,
        lon,
        count: f.properties.point_count,
      };
    }
    return { cluster: false, photo: f.properties.photo };
  });
}

/** Re-render markers when the user pans/zooms (cluster density follows zoom). */
function ZoomWatcher({ onZoom }: { onZoom: (zoom: number) => void }) {
  const map = useMap();
  useEffect(() => {
    const handler = () => onZoom(map.getZoom());
    map.on('zoomend', handler);
    handler();
    return () => {
      map.off('zoomend', handler);
    };
  }, [map, onZoom]);
  return null;
}

function clusterIcon(count: number): L.DivIcon {
  return L.divIcon({
    html: `<div class="photos-map-cluster">${count > 999 ? '999+' : count}</div>`,
    className: 'photos-map-cluster-wrap',
    iconSize: [40, 40],
    iconAnchor: [20, 20],
  });
}

const pointIcon = L.divIcon({
  html: '<div class="photos-map-marker"></div>',
  className: 'photos-map-marker-wrap',
  iconSize: [16, 16],
  iconAnchor: [8, 8],
});

export default function PhotoMapPage({ onSelect }: MapPageProps) {
  const [points, setPoints] = useState<PhotoMapPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [zoom, setZoom] = useState(3);
  const [thumb, setThumb] = useState<PhotoMapPoint | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetchPhotoMapPoints()
      .then((res) => {
        if (alive) {
          setPoints(res.points);
          setError('');
        }
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : 'Could not load map points');
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const index = useMemo<Index>(
    () =>
      new Supercluster<PointProps>({
        radius: 60,
        maxZoom: 17,
      }),
    [],
  );
  const leaves = useMemo(() => buildLeaves(points, zoom, index), [points, zoom, index]);

  if (error) {
    return (
      <div className="photos-empty">
        <h2>Map unavailable</h2>
        <p>{error}</p>
      </div>
    );
  }

  if (!loading && !points.length) {
    return (
      <div className="photos-empty">
        <h2>No geotagged photos</h2>
        <p>
          Photos with GPS coordinates appear here on a world map. Map tiles are
          served by OpenStreetMap — only the map viewport is sent to their
          servers, never your photo library.
        </p>
      </div>
    );
  }

  return (
    <div className="photos-map-wrap">
      {loading && <div className="photos-map-loading">Loading map…</div>}
      <MapContainer
        center={[20, 0]}
        zoom={3}
        minZoom={2}
        worldCopyJump
        className="photos-map"
        attributionControl
      >
        <TileLayer
          attribution={OSM_ATTRIBUTION}
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <ZoomWatcher onZoom={setZoom} />
        {leaves.map((leaf) =>
          leaf.cluster ? (
            <Marker
              key={`c${leaf.id}`}
              position={[leaf.lat, leaf.lon]}
              icon={clusterIcon(leaf.count)}
            >
              <Popup>{leaf.count} photos</Popup>
            </Marker>
          ) : (
            <Marker
              key={leaf.photo.id}
              position={[leaf.photo.lat, leaf.photo.lon]}
              icon={pointIcon}
              eventHandlers={{ click: () => setThumb(leaf.photo) }}
            >
              <Popup>
                {leaf.photo.takenAt
                  ? new Date(leaf.photo.takenAt).toLocaleDateString()
                  : 'Unknown date'}
                <br />
                <button type="button" className="photos-map-popup-open" onClick={() => onSelect(leaf.photo.id)}>
                  Open photo
                </button>
              </Popup>
            </Marker>
          ),
        )}
      </MapContainer>
      {thumb && (
        <div className="photos-map-preview" role="dialog" aria-label="Photo preview">
          <img src={photoThumbUrl(thumb.id, 'preview')} alt="" />
          <button type="button" onClick={() => onSelect(thumb.id)}>Open</button>
          <button type="button" className="photos-map-preview__close" onClick={() => setThumb(null)} aria-label="Close preview">
            ✕
          </button>
        </div>
      )}
    </div>
  );
}
