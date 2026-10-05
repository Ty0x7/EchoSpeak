/** Rich answer blocks ("widgets") shown in chat messages. Data shapes match agent/lean/widgets.py. */

export type WeatherData = {
  location: string;
  units: "C" | "F";
  current: { temp: number; feels?: number | null; code: number; humidity?: number | null; wind?: number | null; wind_unit?: string };
  hourly: { time: string; temp: number; code: number; precip?: number }[];
  daily: { date: string; max: number; min: number; code: number; precip?: number }[];
  as_of?: string;
  source?: string;
};

export type ChartData = {
  kind: "line" | "bar" | "area" | "pie";
  title?: string;
  labels: string[];
  series: { name: string; values: (number | null)[] }[];
  unit?: string;
  y_label?: string;
  as_of?: string;
  source?: string;
  source_url?: string;
};

export type ProductItem = {
  title: string;
  url: string;
  image?: string;
  price: number;
  currency?: string;
  merchant?: string;
  rating?: number | null;
  reviews?: number | null;
};
export type ProductData = { query?: string; items: ProductItem[]; as_of?: string };

export type MediaItem = {
  title?: string;
  url: string;
  thumbnail: string;
  image?: string;
  duration?: string;
  publisher?: string;
  published?: string;
  width?: number | null;
  height?: number | null;
};
export type MediaData = { kind: "video" | "image"; query?: string; items: MediaItem[] };

export type Citation = { title: string; url: string; site?: string; snippet?: string; image?: string };
export type CitationsData = { items: Citation[] };

export type ScoreData = {
  title?: string;
  games: {
    home: string;
    away: string;
    home_score?: number | null;
    away_score?: number | null;
    status?: string;
    start?: string;
    league?: string;
    /** pre | in | post */
    state?: string;
    venue?: string;
    line?: string;
    home_logo?: string;
    away_logo?: string;
    home_abbr?: string;
    away_abbr?: string;
    home_record?: string;
    away_record?: string;
  }[];
  as_of?: string;
};

export type TimelineData = { title?: string; ordered?: boolean; items: { when?: string; title: string; detail?: string }[] };
export type ComparisonData = { title?: string; items: { name: string; url?: string; image?: string; summary?: string; specs: Record<string, string> }[] };
export type StatData = { title?: string; items: { label: string; value: string; unit?: string; change?: string; note?: string }[]; source?: string };
export type MapData = { title?: string; places: { name: string; lat: number; lon: number; address?: string; note?: string }[] };
export type ArtifactRef = { id: string; title: string; kind: string; version: number; language?: string };

export type Widget =
  | { type: "weather"; data: WeatherData }
  | { type: "chart"; data: ChartData }
  | { type: "product_carousel"; data: ProductData }
  | { type: "media"; data: MediaData }
  | { type: "citations"; data: CitationsData }
  | { type: "score_card"; data: ScoreData }
  | { type: "timeline"; data: TimelineData }
  | { type: "comparison"; data: ComparisonData }
  | { type: "stat"; data: StatData }
  | { type: "map"; data: MapData }
  | { type: "creation"; data: { id: string } }
  | { type: "artifact"; data: ArtifactRef };

export type WidgetType = Widget["type"];
