import { NextRequest, NextResponse } from "next/server";
import { Node, Edge } from "@/lib/types/graph";
import { createTriplets } from "@/lib/utils/graph";
import { ZepClient } from "@getzep/zep-cloud";
import { Zep } from "@getzep/zep-cloud";

const supportedResourceTypes = ["user", "graph"] as const;
type ResourceType = (typeof supportedResourceTypes)[number];
const NODE_BATCH_SIZE = 100;
const EDGE_BATCH_SIZE = 100;

const transformSDKNode = (node: Zep.Node): Node => {
  return {
    uuid: node.uuid ?? "",
    name: node.name ?? "",
    summary: node.summary,
    labels: node.labels,
    created_at: node.createdAt ?? "",
    updated_at: "",
    attributes: node.attributes,
  };
};

const transformSDKEdge = (edge: Zep.Edge): Edge => {
  return {
    uuid: edge.uuid ?? "",
    source_node_uuid: edge.sourceNodeUuid ?? "",
    target_node_uuid: edge.targetNodeUuid ?? "",
    type: "",
    name: edge.name ?? "",
    fact: edge.fact,
    episodes: edge.episodeUuids,
    created_at: edge.createdAt ?? "",
    updated_at: "",
    valid_at: edge.validAt,
    expired_at: edge.expiredAt,
    invalid_at: edge.invalidAt,
  };
};

/**
 * v4 addresses a graph by a server-generated UUID. A user has one graph, so
 * the route reads the graph UUID from the user record.
 */
async function resolveGraphUuid(
  type: ResourceType,
  id: string,
  zep: ZepClient,
): Promise<string> {
  if (type === "graph") {
    return id;
  }
  const user = await zep.user.get(id);
  if (!user.graphUuid) {
    throw new Error("The user has no graph UUID");
  }
  return user.graphUuid;
}

async function getAllNodes(graphUuid: string, zep: ZepClient): Promise<Node[]> {
  const nodes: Node[] = [];
  for await (const node of await zep.graph.node.list(graphUuid, {
    limit: NODE_BATCH_SIZE,
    body: {},
  })) {
    nodes.push(transformSDKNode(node));
  }
  return nodes;
}

async function getAllEdges(graphUuid: string, zep: ZepClient): Promise<Edge[]> {
  const edges: Edge[] = [];
  for await (const edge of await zep.graph.edge.list(graphUuid, {
    limit: EDGE_BATCH_SIZE,
    body: {},
  })) {
    edges.push(transformSDKEdge(edge));
  }
  return edges;
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ type: string; id: string }> }
) {
  try {
    const ZEP_API_KEY = process.env.ZEP_API_KEY;

    if (!ZEP_API_KEY) {
      return NextResponse.json(
        { error: "ZEP_API_KEY is not set" },
        { status: 500 }
      );
    }

    const zep = new ZepClient({ apiKey: ZEP_API_KEY });

    const { type: typeParam, id } = await params;

    if (!supportedResourceTypes.includes(typeParam as ResourceType)) {
      return NextResponse.json(
        { error: "Invalid resource type" },
        { status: 400 }
      );
    }
    const type = typeParam as ResourceType;

    const graphUuid = await resolveGraphUuid(type, id, zep);

    // Fetch all nodes and edges of the graph
    const [nodes, edges] = await Promise.all([
      getAllNodes(graphUuid, zep),
      getAllEdges(graphUuid, zep),
    ]);

    if (!nodes.length && !edges.length) {
      return NextResponse.json({ triplets: [] });
    }

    // Combine nodes and edges into triplets
    const triplets = createTriplets(edges, nodes);

    return NextResponse.json({ triplets });
  } catch (error) {
    console.error("Error fetching triplets:", error);
    return NextResponse.json(
      { error: "Failed to fetch graph data" },
      { status: 500 }
    );
  }
}
