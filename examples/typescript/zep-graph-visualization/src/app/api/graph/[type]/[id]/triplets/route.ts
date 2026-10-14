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

const transformSDKNode = (node: Zep.Node): Node => {
  if (!node.uuid) throw new Error("Zep node response is missing a UUID");
  return {
    uuid: node.uuid,
    name: node.name ?? "",
    summary: node.summary ?? "",
    labels: node.labels ?? [],
    created_at: node.createdAt ?? "",
    updated_at: "",
    attributes: node.attributes ?? {},
  };
};

const transformSDKEdge = (edge: Zep.Edge): Edge => {
  if (!edge.uuid || !edge.sourceNodeUuid || !edge.targetNodeUuid) {
    throw new Error("Zep edge response is missing a UUID or endpoint UUID");
  }
  return {
    uuid: edge.uuid,
    source_node_uuid: edge.sourceNodeUuid,
    target_node_uuid: edge.targetNodeUuid,
    type: edge.name ?? "",
    name: edge.name ?? "",
    fact: edge.fact ?? "",
    episodes: edge.episodeUuids ?? [],
    created_at: edge.createdAt ?? "",
    updated_at: "",
    valid_at: edge.validAt ?? "",
    expired_at: edge.expiredAt,
    invalid_at: edge.invalidAt,
  };
};

async function getNodes(
  id: string,
  zep: ZepClient,
  cursor?: string
): Promise<PaginatedResponse<Node>> {
  try {
    const page = await zep.graph.node.list(id, {
      cursor,
      pageSize: NODE_BATCH_SIZE,
    });
    const transformedNodes = page.data.map(transformSDKNode);
    return {
      data: transformedNodes,
      nextCursor: page.response.nextCursor ?? null,
    };
  } catch (error) {
    console.error("Error fetching nodes:", error);
    return { data: [], nextCursor: null };
  }
}

async function getEdges(
  id: string,
  zep: ZepClient,
  cursor?: string
): Promise<PaginatedResponse<Edge>> {
  try {
    const page = await zep.graph.edge.list(id, {
      cursor,
      pageSize: EDGE_BATCH_SIZE,
    });
    const transformedEdges = page.data.map(transformSDKEdge);
    return {
      data: transformedEdges,
      nextCursor: page.response.nextCursor ?? null,
    };
  } catch (error) {
    console.error("Error fetching edges:", error);
    return { data: [], nextCursor: null };
  }
}

async function getAllNodes(
  id: string,
  zep: ZepClient
): Promise<Node[]> {
  let allNodes: Node[] = [];
  let cursor = undefined;
  let hasMore = true;

  while (hasMore) {
    const { data: nodes, nextCursor } = await getNodes(id, zep, cursor);
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
  id: string,
  zep: ZepClient
): Promise<Edge[]> {
  let allEdges: Edge[] = [];
  let cursor = undefined;
  let hasMore = true;

  while (hasMore) {
    const { data: edges, nextCursor } = await getEdges(id, zep, cursor);
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

    // Fetch all nodes and edges using the batch completion wrappers
    const [nodes, edges] = await Promise.all([
      getAllNodes(id, zep),
      getAllEdges(id, zep),
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
