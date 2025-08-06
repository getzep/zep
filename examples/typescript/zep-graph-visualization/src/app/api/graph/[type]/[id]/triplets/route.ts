import { NextRequest, NextResponse } from "next/server";
import { Node, Edge } from "@/lib/types/graph";
import { createTriplets } from "@/lib/utils/graph";
import { ZepClient } from "@getzep/zep-cloud";
import { Zep } from "@getzep/zep-cloud";

interface PaginatedResponse<T> {
  data: T[];
  nextCursor: string | null;
}

const supportedResourceTypes = ["user", "group"] as const;
type ResourceType = (typeof supportedResourceTypes)[number];
const NODE_BATCH_SIZE = 100;
const EDGE_BATCH_SIZE = 100;

const transformSDKNode = (node: Zep.EntityNode): Node => {
  return {
    uuid: node.uuid,
    name: node.name,
    summary: node.summary,
    labels: node.labels,
    created_at: node.createdAt,
    updated_at: "",
    attributes: node.attributes,
  };
};

const transformSDKEdge = (edge: Zep.EntityEdge): Edge => {
  return {
    uuid: edge.uuid,
    source_node_uuid: edge.sourceNodeUuid,
    target_node_uuid: edge.targetNodeUuid,
    type: "",
    name: edge.name,
    fact: edge.fact,
    episodes: edge.episodes,
    created_at: edge.createdAt,
    updated_at: "",
    valid_at: edge.validAt,
    expired_at: edge.expiredAt,
    invalid_at: edge.invalidAt,
  };
};

async function getNodes(
  type: ResourceType,
  id: string,
  zep: ZepClient,
  cursor?: string
): Promise<PaginatedResponse<Node>> {
  try {
    let nodes;
    if (type === "user") {
      nodes = await zep.graph.node.getByUserId(id, {
        uuidCursor: cursor || "",
        limit: NODE_BATCH_SIZE,
      });
    } else {
      nodes = await zep.graph.node.getByGraphId(id, {
        uuidCursor: cursor || "",
        limit: NODE_BATCH_SIZE,
      });
    }
    
    const transformedNodes = nodes.map(transformSDKNode);
    return {
      data: transformedNodes,
      nextCursor: transformedNodes.length > 0 ? transformedNodes[transformedNodes.length - 1].uuid : null,
    };
  } catch (error) {
    console.error("Error fetching nodes:", error);
    return { data: [], nextCursor: null };
  }
}

async function getEdges(
  type: ResourceType, 
  id: string, 
  zep: ZepClient,
  cursor?: string
): Promise<PaginatedResponse<Edge>> {
  try {
    let edges;
    if (type === "user") {
      edges = await zep.graph.edge.getByUserId(id, {
        uuidCursor: cursor || "",
        limit: EDGE_BATCH_SIZE,
      });
    } else {
      edges = await zep.graph.edge.getByGraphId(id, {
        uuidCursor: cursor || "",
        limit: EDGE_BATCH_SIZE,
      });
    }
    
    const transformedEdges = edges.map(transformSDKEdge);
    return {
      data: transformedEdges,
      nextCursor: transformedEdges.length > 0 ? transformedEdges[transformedEdges.length - 1].uuid : null,
    };
  } catch (error) {
    console.error("Error fetching edges:", error);
    return { data: [], nextCursor: null };
  }
}

async function getAllNodes(
  type: ResourceType,
  id: string,
  zep: ZepClient
): Promise<Node[]> {
  let allNodes: Node[] = [];
  let cursor = undefined;
  let hasMore = true;

  while (hasMore) {
    const { data: nodes, nextCursor } = await getNodes(type, id, zep, cursor);
    allNodes = [...allNodes, ...nodes];

    if (nextCursor === null || nodes.length === 0) {
      hasMore = false;
    } else {
      cursor = nextCursor;
    }
  }

  return allNodes;
}

async function getAllEdges(
  type: ResourceType,
  id: string,
  zep: ZepClient
): Promise<Edge[]> {
  let allEdges: Edge[] = [];
  let cursor = undefined;
  let hasMore = true;

  while (hasMore) {
    const { data: edges, nextCursor } = await getEdges(type, id, zep, cursor);
    allEdges = [...allEdges, ...edges];

    if (nextCursor === null || edges.length === 0) {
      hasMore = false;
    } else {
      cursor = nextCursor;
    }
  }

  return allEdges;
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ type: ResourceType; id: string }> }
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

    const { type, id } = await params;

    if (!supportedResourceTypes.includes(type as ResourceType)) {
      return NextResponse.json(
        { error: "Invalid resource type" },
        { status: 400 }
      );
    }

    // Fetch all nodes and edges using the batch completion wrappers
    const [nodes, edges] = await Promise.all([
      getAllNodes(type, id, zep),
      getAllEdges(type, id, zep),
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
