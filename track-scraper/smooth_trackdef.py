#!/usr/bin/env python3
"""
Smooth trackdef centerline — interpolate to 1 point per meter.
Takes the original centerline points and creates a smooth version
with points every 1 meter along the path.
"""
import json
import math
import sys

def clean_overlap(points):
    """Detect and remove overlapping points if the track goes past the start line."""
    if len(points) < 50:
        return points
        
    start_p = points[0]
    closest_idx = -1
    closest_dist = float('inf')
    
    # Only search the second half of the points to avoid false positives
    search_start = len(points) // 2
    
    for i in range(search_start, len(points)):
        p = points[i]
        dx = p['x'] - start_p['x']
        dy = p['y'] - start_p['y']
        dist = math.sqrt(dx*dx + dy*dy)
        if dist < closest_dist:
            closest_dist = dist
            closest_idx = i
            
    # If the closest point is within 20 meters, it means the track looped back.
    if closest_dist < 20.0 and closest_idx != len(points) - 1:
        print(f"Overlap detected! Closest point is at index {closest_idx} (dist to start: {closest_dist:.2f}m).")
        print(f"Truncating {len(points) - closest_idx - 1} overlapping points.")
        cleaned = points[:closest_idx + 1]
        
        # To perfectly close the loop, append a copy of the start point
        dx = start_p['x'] - cleaned[-1]['x']
        dy = start_p['y'] - cleaned[-1]['y']
        gap_dist = math.sqrt(dx*dx + dy*dy)
        
        last_dist = cleaned[-1]['dist'] + gap_dist
        cleaned.append({
            "x": start_p['x'],
            "y": start_p['y'],
            "z": start_p['z'],
            "dist": round(last_dist, 2)
        })
        return cleaned
        
    return points

def interpolate_centerline(points, interval=1.0):
    """Interpolate points to have one point every `interval` meters."""
    if len(points) < 2:
        return points, 0.0
    
    result = []
    total_dist = 0.0
    
    # Add first point
    result.append({
        "x": round(points[0]["x"], 2),
        "y": round(points[0]["y"], 2),
        "z": round(points[0]["z"], 2),
        "dist": 0.0
    })
    
    next_target_dist = interval
    
    for i in range(len(points) - 1):
        p1 = points[i]
        p2 = points[i + 1]
        
        # Segment length (2D only — matches backend projection)
        dx = p2["x"] - p1["x"]
        dy = p2["y"] - p1["y"]
        dz = p2["z"] - p1["z"]
        seg_len = math.sqrt(dx*dx + dy*dy)  # XY only, Z ignored
        
        if seg_len == 0:
            continue
        
        seg_start_dist = total_dist
        seg_end_dist = total_dist + seg_len
        
        # Insert interpolated points within this segment
        while next_target_dist <= seg_end_dist:
            # How far along this segment
            t = (next_target_dist - seg_start_dist) / seg_len
            t = max(0.0, min(1.0, t))
            
            result.append({
                "x": round(p1["x"] + dx * t, 2),
                "y": round(p1["y"] + dy * t, 2),
                "z": round(p1["z"] + dz * t, 2),
                "dist": round(next_target_dist, 2)
            })
            next_target_dist += interval
        
        total_dist = seg_end_dist
    
    # Add last point if not already added
    if result[-1]["dist"] < total_dist - 0.5:
        last_original = points[-1]
        result.append({
            "x": round(last_original["x"], 2),
            "y": round(last_original["y"], 2),
            "z": round(last_original["z"], 2),
            "dist": round(total_dist, 2)
        })
    
    return result, round(total_dist, 2)


def main():
    input_file = sys.argv[1] if len(sys.argv) > 1 else "custom_track.trackdef.json"
    output_file = input_file.replace(".trackdef.json", "_smooth.trackdef.json")
    
    with open(input_file, "r") as f:
        trackdef = json.load(f)
    
    original_count = len(trackdef["centerline"])
    original_length = trackdef["total_length_m"]
    
    # Auto-clean overlaps before interpolating
    trackdef["centerline"] = clean_overlap(trackdef["centerline"])
    
    # Interpolate centerline
    smoothed, new_total = interpolate_centerline(trackdef["centerline"], interval=1.0)
    trackdef["centerline"] = smoothed
    trackdef["total_length_m"] = new_total
    
    # Regenerate SVG path
    svg_parts = []
    for i, pt in enumerate(smoothed):
        prefix = "M" if i == 0 else "L"
        svg_parts.append(f"{prefix} {pt['x']:.2f} {pt['y']:.2f}")
    svg_parts.append("Z")
    trackdef["svg_path"] = " ".join(svg_parts)

    # Interpolate pit_lane if exists
    if "pit_lane" in trackdef and trackdef["pit_lane"]:
        smoothed_pit, pit_len = interpolate_centerline(trackdef["pit_lane"], interval=1.0)
        trackdef["pit_lane"] = smoothed_pit
        
        # Regenerate pit SVG path
        pit_svg_parts = []
        for i, pt in enumerate(smoothed_pit):
            prefix = "M" if i == 0 else "L"
            pit_svg_parts.append(f"{prefix} {pt['x']:.2f} {pt['y']:.2f}")
        trackdef["pit_svg_path"] = " ".join(pit_svg_parts)
    
    # Modifikasi agar point JSON dirender sejajar dalam 1 baris
    import re
    json_str = json.dumps(trackdef, indent=2)
    
    # regex format for { "x": ..., "y": ..., "z": ..., "dist": ... }
    json_str = re.sub(
        r'\{\s*"x":\s*([^,]+),\s*"y":\s*([^,]+),\s*"z":\s*([^,\}]+)(?:,\s*"dist":\s*([^\}]+))?\s*\}',
        lambda m: f'{{"x": {m.group(1).strip()}, "y": {m.group(2).strip()}, "z": {m.group(3).strip()}' + (f', "dist": {m.group(4).strip()}' if m.group(4) else '') + '}',
        json_str
    )
    
    # Write output
    with open(output_file, "w") as f:
        f.write(json_str)
    
    print(f"Original:  {original_count} points, {original_length}m")
    print(f"Smoothed:  {len(smoothed)} points, {new_total}m")
    if "pit_lane" in trackdef:
        print(f"Pit Lane:  {len(trackdef['pit_lane'])} points")
    print(f"Interval:  ~1m per point")
    print(f"Output:    {output_file}")


if __name__ == "__main__":
    main()