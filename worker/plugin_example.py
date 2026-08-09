"""Example locally-installed task plugin.

Enable with:
    export SWARM_PLUGIN_MODULES=plugin_example
"""
from swarm_plugin import task


@task("vector_sum")
def vector_sum(payload):
    values = payload.get("values", [])
    return {"sum": sum(float(x) for x in values), "count": len(values)}
