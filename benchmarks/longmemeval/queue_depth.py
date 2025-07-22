import json

# import local json file
with open("queue_metrics.json", "r") as f:
    data = json.load(f)
    gwf = data["GraphWorkflow-1fe79d02-2163-4b3b-8530-677551b358c6"]

    # print length of keys
    print(len(gwf.keys()))
    # avg of all values

    total = 0
    for key, value in gwf.items():
        total += value
    print(total / len(gwf))

    # format long number
    print("total tasks in queue -> ", f"{total:,}")
