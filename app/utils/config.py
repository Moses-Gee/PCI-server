# import math

# items = [
#     {"distress": "alligator", "severity": "H", "density": 15.0},
#     {"distress": "long_trans", "severity": "M", "density": 8.0},
#     {"distress": "pothole", "severity": "L", "density": 0.2},
#     {"distress": "long_trans", "severity": "M", "density": 9.0},
#     {"distress": "long_trans", "severity": "M", "density": 60.0},
#     {"distress": "long_trans", "severity": "M", "density": 44.0},
# ]


# dvs = sorted([e["density"] for e in items], reverse=True)
# print(dvs)

# hdv = dvs[0]
# m = min(10, 1 + (9 / 98) * (100 - hdv))
# num_to_keep = math.ceil(m)

# # Truncate to m deducts (last one scaled by fractional part of m)
# working = dvs[:num_to_keep]
# print(working)

# current_dvs = list(working)

# for i in reversed(range(len(current_dvs))):
#     print(current_dvs[i])
