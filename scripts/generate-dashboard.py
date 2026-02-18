#!/usr/bin/env python3
"""Generate Kibana dashboard NDJSON for F1 Telemetry."""
import json
import sys

DATA_VIEW_ID = "f1-telemetry-metrics"
DASHBOARD_ID = "f1-telemetry-dashboard"

def lens_ref(layer_id="layer1"):
    return [{"type": "index-pattern", "id": DATA_VIEW_ID,
             "name": f"indexpattern-datasource-layer-{layer_id}"}]

def metric_col(label, field, sort_field="@timestamp"):
    return {
        "label": label, "dataType": "number",
        "operationType": "last_value", "sourceField": field,
        "isBucketed": False, "scale": "ratio",
        "params": {"sortField": sort_field}
    }

def ts_col(label, field):
    return {
        "label": label, "dataType": "date",
        "operationType": "date_histogram", "sourceField": field,
        "isBucketed": True, "scale": "interval",
        "params": {"interval": "auto", "includeEmptyRows": True}
    }

MIGRATION = {"typeMigrationVersion": "10.1.0", "coreMigrationVersion": "8.8.0"}

def fb_state(layers_dict):
    return {"formBased": {
        "currentIndexPatternId": DATA_VIEW_ID,
        "layers": {k: {**v, "indexPatternId": DATA_VIEW_ID} for k, v in layers_dict.items()}
    }}

def make_metric_panel(panel_id, title, field, label=None,
                      suffix="", color=None, fmt=None):
    lab = label or title
    viz = {
        "layerId": "layer1", "layerType": "data",
        "metricAccessor": "col1"
    }
    if suffix:
        viz["subtitle"] = suffix
    if color:
        viz["color"] = color
    if fmt:
        viz["valueFormat"] = fmt
    return {
        "type": "lens", "id": panel_id, **MIGRATION,
        "attributes": {
            "title": title, "visualizationType": "lnsMetric",
            "state": {
                "datasourceStates": fb_state({"layer1": {
                    "columns": {"col1": metric_col(lab, field)},
                    "columnOrder": ["col1"], "incompleteColumns": {}
                }}),
                "visualization": viz,
                "query": {"query": "", "language": "kuery"}, "filters": []
            }, "description": ""
        },
        "references": lens_ref()
    }

def make_gauge_panel(panel_id, title, field, label=None,
                     shape="semicircle", color_stops=None, suffix="",
                     range_max=100):
    lab = label or title
    palette = None
    if color_stops:
        stops = []
        cs = []
        for i, (color, stop) in enumerate(color_stops):
            cs.append({"color": color, "stop": stop})
            next_stop = color_stops[i+1][1] if i+1 < len(color_stops) else range_max
            stops.append({"color": color, "stop": next_stop})
        palette = {
            "name": "custom", "type": "palette",
            "params": {
                "steps": len(color_stops), "name": "custom",
                "reverse": False, "rangeType": "number",
                "rangeMin": 0, "rangeMax": range_max,
                "colorStops": cs, "stops": stops
            }
        }

    viz = {
        "layerId": "layer1", "layerType": "data",
        "metricAccessor": "col1",
        "shape": shape,
        "ticksPosition": "auto", "labelMajorMode": "auto"
    }
    if palette:
        viz["palette"] = palette
    return {
        "type": "lens", "id": panel_id, **MIGRATION,
        "attributes": {
            "title": title, "visualizationType": "lnsGauge",
            "state": {
                "datasourceStates": fb_state({"layer1": {
                    "columns": {"col1": metric_col(lab, field)},
                    "columnOrder": ["col1"], "incompleteColumns": {}
                }}),
                "visualization": viz,
                "query": {"query": "", "language": "kuery"}, "filters": []
            }, "description": ""
        },
        "references": lens_ref()
    }

def make_xy_panel(panel_id, title, line_field, line_label,
                  bar_field, bar_label):
    return {
        "type": "lens", "id": panel_id, **MIGRATION,
        "attributes": {
            "title": title, "visualizationType": "lnsXY",
            "state": {
                "datasourceStates": fb_state({
                    "line_layer": {
                        "columns": {
                            "ts": ts_col("Timestamp", "@timestamp"),
                            "line_val": metric_col(line_label, line_field)
                        },
                        "columnOrder": ["ts", "line_val"],
                        "incompleteColumns": {}
                    },
                    "bar_layer": {
                        "columns": {
                            "ts2": ts_col("Timestamp", "@timestamp"),
                            "bar_val": metric_col(bar_label, bar_field)
                        },
                        "columnOrder": ["ts2", "bar_val"],
                        "incompleteColumns": {}
                    }
                }),
                "visualization": {
                    "legend": {"isVisible": False},
                    "preferredSeriesType": "line",
                    "layers": [
                        {
                            "layerId": "line_layer", "layerType": "data",
                            "seriesType": "line",
                            "xAccessor": "ts", "accessors": ["line_val"],
                            "yConfig": [{"forAccessor": "line_val", "axisMode": "left"}]
                        },
                        {
                            "layerId": "bar_layer", "layerType": "data",
                            "seriesType": "bar",
                            "xAccessor": "ts2", "accessors": ["bar_val"],
                            "yConfig": [{"forAccessor": "bar_val", "axisMode": "right"}]
                        }
                    ]
                },
                "query": {"query": "", "language": "kuery"}, "filters": []
            }, "description": ""
        },
        "references": [
            {"type": "index-pattern", "id": DATA_VIEW_ID,
             "name": "indexpattern-datasource-layer-line_layer"},
            {"type": "index-pattern", "id": DATA_VIEW_ID,
             "name": "indexpattern-datasource-layer-bar_layer"}
        ]
    }


# --- Vega car temperature diagram ---

VEGA_SPEC = r"""{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "title": {"text": "Car Temperatures", "anchor": "middle", "fontSize": 16},
  "width": "container",
  "height": 350,
  "padding": 20,
  "data": {
    "url": {
      "%context%": true,
      "%timefield%": "@timestamp",
      "index": "metrics-f1_telemetry.otel-default",
      "body": {
        "size": 0,
        "aggs": {
          "brake_fl": {"top_metrics": {"metrics": {"field": "metrics.f1.brakes_temperature3"}, "sort": {"@timestamp": "desc"}}},
          "brake_fr": {"top_metrics": {"metrics": {"field": "metrics.f1.brakes_temperature4"}, "sort": {"@timestamp": "desc"}}},
          "brake_rl": {"top_metrics": {"metrics": {"field": "metrics.f1.brakes_temperature1"}, "sort": {"@timestamp": "desc"}}},
          "brake_rr": {"top_metrics": {"metrics": {"field": "metrics.f1.brakes_temperature2"}, "sort": {"@timestamp": "desc"}}},
          "tyre_s_fl": {"top_metrics": {"metrics": {"field": "metrics.f1.tyres_surface_temperature3"}, "sort": {"@timestamp": "desc"}}},
          "tyre_s_fr": {"top_metrics": {"metrics": {"field": "metrics.f1.tyres_surface_temperature4"}, "sort": {"@timestamp": "desc"}}},
          "tyre_s_rl": {"top_metrics": {"metrics": {"field": "metrics.f1.tyres_surface_temperature1"}, "sort": {"@timestamp": "desc"}}},
          "tyre_s_rr": {"top_metrics": {"metrics": {"field": "metrics.f1.tyres_surface_temperature2"}, "sort": {"@timestamp": "desc"}}},
          "tyre_i_fl": {"top_metrics": {"metrics": {"field": "metrics.f1.tyres_inner_temperature3"}, "sort": {"@timestamp": "desc"}}},
          "tyre_i_fr": {"top_metrics": {"metrics": {"field": "metrics.f1.tyres_inner_temperature4"}, "sort": {"@timestamp": "desc"}}},
          "tyre_i_rl": {"top_metrics": {"metrics": {"field": "metrics.f1.tyres_inner_temperature1"}, "sort": {"@timestamp": "desc"}}},
          "tyre_i_rr": {"top_metrics": {"metrics": {"field": "metrics.f1.tyres_inner_temperature2"}, "sort": {"@timestamp": "desc"}}}
        }
      }
    },
    "format": {"property": "aggregations"}
  },
  "transform": [
    {"calculate": "datum.brake_fl.top[0].metrics['metrics.f1.brakes_temperature3']", "as": "brake_fl_v"},
    {"calculate": "datum.brake_fr.top[0].metrics['metrics.f1.brakes_temperature4']", "as": "brake_fr_v"},
    {"calculate": "datum.brake_rl.top[0].metrics['metrics.f1.brakes_temperature1']", "as": "brake_rl_v"},
    {"calculate": "datum.brake_rr.top[0].metrics['metrics.f1.brakes_temperature2']", "as": "brake_rr_v"},
    {"calculate": "datum.tyre_s_fl.top[0].metrics['metrics.f1.tyres_surface_temperature3']", "as": "surf_fl_v"},
    {"calculate": "datum.tyre_s_fr.top[0].metrics['metrics.f1.tyres_surface_temperature4']", "as": "surf_fr_v"},
    {"calculate": "datum.tyre_s_rl.top[0].metrics['metrics.f1.tyres_surface_temperature1']", "as": "surf_rl_v"},
    {"calculate": "datum.tyre_s_rr.top[0].metrics['metrics.f1.tyres_surface_temperature2']", "as": "surf_rr_v"},
    {"calculate": "datum.tyre_i_fl.top[0].metrics['metrics.f1.tyres_inner_temperature3']", "as": "inner_fl_v"},
    {"calculate": "datum.tyre_i_fr.top[0].metrics['metrics.f1.tyres_inner_temperature4']", "as": "inner_fr_v"},
    {"calculate": "datum.tyre_i_rl.top[0].metrics['metrics.f1.tyres_inner_temperature1']", "as": "inner_rl_v"},
    {"calculate": "datum.tyre_i_rr.top[0].metrics['metrics.f1.tyres_inner_temperature2']", "as": "inner_rr_v"},
    {"fold": [
      "brake_fl_v","brake_fr_v","brake_rl_v","brake_rr_v",
      "surf_fl_v","surf_fr_v","surf_rl_v","surf_rr_v",
      "inner_fl_v","inner_fr_v","inner_rl_v","inner_rr_v"
    ]},
    {"calculate": "split(datum.key, '_')[0]", "as": "type"},
    {"calculate": "upper(split(datum.key, '_')[1])", "as": "wheel"},
    {"calculate": "datum.type == 'brake' ? 'Brake' : datum.type == 'surf' ? 'Surface' : 'Inner'", "as": "label"},
    {"calculate": "datum.wheel == 'FL' ? 0 : datum.wheel == 'FR' ? 1 : datum.wheel == 'RL' ? 2 : 3", "as": "wheel_idx"},
    {"calculate": "datum.type == 'brake' ? 0 : datum.type == 'surf' ? 1 : 2", "as": "type_idx"},
    {"calculate": "datum.wheel_idx < 2 ? 'Front' : 'Rear'", "as": "row"},
    {"calculate": "datum.wheel_idx % 2 == 0 ? 'Left' : 'Right'", "as": "col"},
    {"calculate": "datum.type == 'brake' ? (datum.value <= 100 ? '#3B82F6' : datum.value <= 700 ? '#22C55E' : datum.value <= 900 ? '#EAB308' : datum.value <= 1000 ? '#F97316' : '#EF4444') : datum.type == 'surf' ? (datum.value <= 80 ? '#3B82F6' : datum.value <= 120 ? '#22C55E' : datum.value <= 125 ? '#EAB308' : datum.value <= 130 ? '#F97316' : '#EF4444') : (datum.value <= 60 ? '#3B82F6' : datum.value <= 110 ? '#22C55E' : datum.value <= 120 ? '#EAB308' : datum.value <= 130 ? '#F97316' : '#EF4444')", "as": "temp_color"}
  ],
  "facet": {
    "row": {"field": "row", "type": "nominal", "sort": ["Front", "Rear"], "header": {"title": null, "labelFontSize": 14}},
    "column": {"field": "col", "type": "nominal", "sort": ["Left", "Right"], "header": {"title": null, "labelFontSize": 14}}
  },
  "spec": {
    "width": 220,
    "height": 120,
    "layer": [
      {
        "mark": {"type": "rect", "cornerRadius": 6, "stroke": "#666", "strokeWidth": 1},
        "encoding": {
          "x": {"field": "type_idx", "type": "ordinal", "axis": null, "scale": {"padding": 0.2}},
          "color": {"field": "temp_color", "type": "nominal", "scale": null},
          "tooltip": [
            {"field": "label", "type": "nominal", "title": "Type"},
            {"field": "value", "type": "quantitative", "title": "Temp (°C)", "format": ".0f"},
            {"field": "wheel", "type": "nominal", "title": "Wheel"}
          ]
        }
      },
      {
        "mark": {"type": "text", "fontSize": 18, "fontWeight": "bold", "dy": -8},
        "encoding": {
          "x": {"field": "type_idx", "type": "ordinal", "axis": null},
          "text": {"field": "value", "type": "quantitative", "format": ".0f"},
          "color": {"value": "white"}
        }
      },
      {
        "mark": {"type": "text", "fontSize": 10, "dy": 10},
        "encoding": {
          "x": {"field": "type_idx", "type": "ordinal", "axis": null},
          "text": {"field": "label", "type": "nominal"},
          "color": {"value": "#ccc"}
        }
      }
    ]
  }
}"""


def make_vega_panel(panel_id, title):
    return {
        "type": "visualization", "id": panel_id,
        "attributes": {
            "title": title,
            "visState": json.dumps({
                "title": title,
                "type": "vega",
                "aggs": [],
                "params": {"spec": VEGA_SPEC}
            }),
            "uiStateJSON": "{}",
            "description": "",
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps({
                    "query": {"query": "", "language": "kuery"},
                    "filter": []
                })
            }
        },
        "references": []
    }


# --- Dashboard assembly ---

def build_all():
    objects = []

    # Row 0: Key stats bar
    objects.append(make_metric_panel("f1-gear", "Gear", "metrics.f1.gear"))
    objects.append(make_gauge_panel("f1-speed-mph", "Speed", "metrics.f1.speed",
        label="Speed (mph)", shape="semicircle",
        color_stops=[("#54B399", 0), ("#6DCCB1", 150), ("#E7664C", 180), ("#DA1B20", 225)],
        range_max=250))
    objects.append(make_gauge_panel("f1-rpm", "RPM", "metrics.f1.engine_rpm",
        shape="semicircle",
        color_stops=[("#54B399", 0), ("#D6BF57", 9000), ("#E7664C", 11500), ("#DA1B20", 13000)],
        range_max=15000))
    objects.append(make_gauge_panel("f1-throttle", "Throttle", "metrics.f1.throttle",
        label="Throttle %", shape="horizontalBullet",
        color_stops=[("#DA1B20", 0), ("#E7664C", 25), ("#D6BF57", 50), ("#54B399", 75)],
        range_max=1))
    objects.append(make_gauge_panel("f1-brake", "Brake", "metrics.f1.brake",
        label="Brake %", shape="horizontalBullet",
        color_stops=[("#54B399", 0), ("#D6BF57", 0.2), ("#E7664C", 0.6), ("#DA1B20", 0.8)],
        range_max=1))
    objects.append(make_metric_panel("f1-sector", "Sector", "metrics.f1.sector"))
    objects.append(make_metric_panel("f1-lap-time", "Lap Time", "metrics.f1.current_lap_time_in_ms",
        label="Lap Time (ms)"))

    # Row 1: Time series + environment
    objects.append(make_xy_panel("f1-rpm-speed", "RPM / Speed (kph)",
        "metrics.f1.engine_rpm", "RPM", "metrics.f1.speed", "Speed (kph)"))
    objects.append(make_xy_panel("f1-speed-brake", "Speed (kph) / Brake",
        "metrics.f1.speed", "Speed (kph)", "metrics.f1.brake", "Brake"))
    objects.append(make_gauge_panel("f1-engine-temp", "Engine Temp", "metrics.f1.engine_temperature",
        shape="semicircle",
        color_stops=[("#3B82F6", 0), ("#54B399", 50), ("#D6BF57", 125), ("#E7664C", 150), ("#DA1B20", 170)],
        range_max=200))
    objects.append(make_metric_panel("f1-air-temp", "Air Temp", "metrics.f1.air_temperature",
        suffix="°C"))
    objects.append(make_metric_panel("f1-track-temp", "Track Temp", "metrics.f1.track_temperature",
        suffix="°C"))
    objects.append(make_metric_panel("f1-current-lap", "Current Lap", "metrics.f1.current_lap_num"))

    # Row 3+4: Vega car diagram
    objects.append(make_vega_panel("f1-car-temps", "Car Temperatures"))

    # Dashboard
    panels = [
        # Row 0: key stats (y=0, h=6)
        grid("f1-gear",       0,  0, 4,  6),
        grid("f1-speed-mph",  4,  0, 8,  6),
        grid("f1-rpm",       12,  0, 8,  6),
        grid("f1-throttle",  20,  0, 8,  6),
        grid("f1-brake",     28,  0, 8,  6),
        grid("f1-sector",    36,  0, 4,  6),
        grid("f1-lap-time",  40,  0, 8,  6),

        # Row 1: time series + env (y=6, h=10)
        grid("f1-rpm-speed",    0,  6, 16, 10),
        grid("f1-speed-brake", 16,  6, 16, 10),
        grid("f1-engine-temp", 32,  6, 6,  10),
        grid("f1-air-temp",    38,  6, 5,  10),
        grid("f1-track-temp",  43,  6, 5,  10),

        # Row 1b: current lap
        grid("f1-current-lap", 38, 16, 10, 5),

        # Row 3+4: Vega car temps (y=16, h=16)
        grid("f1-car-temps",    0, 21, 48, 16),
    ]

    refs = []
    for i, p in enumerate(panels):
        refs.append({
            "name": f"panel_{p['panelIndex']}",
            "type": p["type"],
            "id": p["panelRefName"]
        })

    dashboard = {
        "type": "dashboard", "id": DASHBOARD_ID,
        "attributes": {
            "title": "F1 Telemetry - ELK on Track",
            "description": "Real-time F1 car telemetry dashboard with speed, RPM, temperatures, and brake data.",
            "panelsJSON": json.dumps([{k: v for k, v in p.items() if k != "panelRefName"} for p in panels]),
            "optionsJSON": json.dumps({"useMargins": True, "syncColors": True, "syncCursor": True, "syncTooltips": True, "hidePanelTitles": False}),
            "timeRestore": True,
            "timeTo": "now",
            "timeFrom": "now-1m",
            "refreshInterval": {"pause": False, "value": 5000},
            "controlGroupInput": {
                "controlStyle": "oneLine",
                "chainingSystem": "HIERARCHICAL",
                "showApplySelections": False,
                "ignoreParentSettingsJSON": json.dumps({"ignoreFilters": False, "ignoreQuery": False, "ignoreTimerange": False, "ignoreValidations": False}),
                "panelsJSON": json.dumps({
                    "0": {
                        "type": "optionsListControl",
                        "order": 0,
                        "width": "medium",
                        "grow": True,
                        "explicitInput": {
                            "id": "0",
                            "fieldName": "resource.attributes.f1.hostname",
                            "title": "Rig",
                            "enhancements": {},
                            "selectedOptions": [],
                            "existsSelected": False,
                            "singleSelect": True
                        }
                    }
                })
            },
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps({"query": {"query": "", "language": "kuery"}, "filter": []})
            }
        },
        "references": [{"name": f"panel_{p['panelIndex']}", "type": "lens" if p["type"] == "lens" else "visualization", "id": p["panelRefName"]} for p in panels]
    }

    objects.append(dashboard)
    return objects


def grid(ref_id, x, y, w, h):
    return {
        "type": "lens" if not ref_id.startswith("f1-car") else "visualization",
        "gridData": {"x": x, "y": y, "w": w, "h": h, "i": ref_id},
        "panelIndex": ref_id,
        "embeddableConfig": {},
        "panelRefName": ref_id
    }


def main():
    objects = build_all()
    output = sys.argv[1] if len(sys.argv) > 1 else "-"

    lines = []
    for obj in objects:
        lines.append(json.dumps(obj, separators=(",", ":")))

    ndjson = "\n".join(lines) + "\n"

    if output == "-":
        sys.stdout.write(ndjson)
    else:
        with open(output, "w") as f:
            f.write(ndjson)
        print(f"Written {len(objects)} objects to {output}", file=sys.stderr)


if __name__ == "__main__":
    main()
