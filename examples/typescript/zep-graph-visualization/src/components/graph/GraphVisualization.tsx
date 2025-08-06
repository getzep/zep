"use client";

import { useState, useMemo, forwardRef } from "react";
import { Graph, GraphRef } from "@/components/graph/Graph";
import { GraphPopovers } from "@/components/graph/GraphPopovers";
import type {
  RawTriplet,
  NodePopupContent,
  EdgePopupContent,
} from "@/lib/types/graph";
import { toGraphTriplets } from "@/lib/utils/graph";
import { createLabelColorMap, getNodeColor } from "@/lib/utils/nodeColors";
import { useTheme } from "next-themes";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";

interface GraphVisualizationProps {
  triplets: RawTriplet[];
  width?: number;
  height?: number;
  zoomOnMount?: boolean;
  className?: string;
}

// eslint-disable-next-line react/display-name
export const GraphVisualization = forwardRef<GraphRef, GraphVisualizationProps>(
  (
    {
      triplets,
      width = window.innerWidth * 0.85,
      height = window.innerHeight * 0.85,
      zoomOnMount = true,
      className = "border border-border rounded-md h-[85vh] overflow-hidden relative",
    },
    ref
  ) => {
    const { resolvedTheme } = useTheme();
    const isDarkMode = resolvedTheme === "dark";

    // Graph state for popovers
    const [showNodePopup, setShowNodePopup] = useState<boolean>(false);
    const [showEdgePopup, setShowEdgePopup] = useState<boolean>(false);
    const [nodePopupContent, setNodePopupContent] =
      useState<NodePopupContent | null>(null);
    const [edgePopupContent, setEdgePopupContent] =
      useState<EdgePopupContent | null>(null);

    // Convert raw triplets to graph triplets
    const graphTriplets = useMemo(() => toGraphTriplets(triplets), [triplets]);

    // Extract all unique labels from triplets
    const allLabels = useMemo(() => {
      const labels = new Set<string>();
      labels.add("Entity"); // Always include Entity as default

      graphTriplets.forEach((triplet) => {
        if (triplet.source.primaryLabel)
          labels.add(triplet.source.primaryLabel);
        if (triplet.target.primaryLabel)
          labels.add(triplet.target.primaryLabel);
      });

      return Array.from(labels).sort((a, b) => {
        // Always put "Entity" first
        if (a === "Entity") return -1;
        if (b === "Entity") return 1;
        // Sort others alphabetically
        return a.localeCompare(b);
      });
    }, [graphTriplets]);

    // Create a shared label color map
    const sharedLabelColorMap = useMemo(() => {
      return createLabelColorMap(allLabels);
    }, [allLabels]);

    // Handle node click
    const handleNodeClick = (nodeId: string) => {
      // Find the triplet that contains this node
      const triplet = triplets.find(
        (t) => t.sourceNode.uuid === nodeId || t.targetNode.uuid === nodeId
      );

      if (!triplet) return;

      // Determine which node was clicked (source or target)
      const node =
        triplet.sourceNode.uuid === nodeId
          ? triplet.sourceNode
          : triplet.targetNode;

      // Set popup content and show the popup
      setNodePopupContent({
        id: nodeId,
        node: node,
      });
      setShowNodePopup(true);
      setShowEdgePopup(false);
    };

    // Handle edge click
    const handleEdgeClick = (edgeId: string) => {
      // Find the triplet that contains this edge
      const triplet = triplets.find((t) => t.edge.uuid === edgeId);

      if (!triplet) return;

      // Set popup content and show the popup
      setEdgePopupContent({
        id: edgeId,
        source: triplet.sourceNode,
        target: triplet.targetNode,
        relation: triplet.edge,
      });
      setShowEdgePopup(true);
      setShowNodePopup(false);
    };

    // Handle popover close
    const handlePopoverClose = () => {
      setShowNodePopup(false);
      setShowEdgePopup(false);
    };
    return (
      <div className={className}>
        {/* Entity Types Legend Button */}
        <div className="absolute top-4 left-4 z-50">
          <HoverCard>
            <HoverCardTrigger asChild>
              <button className="text-xs px-2.5 py-1 rounded-md bg-primary/10 text-primary hover:bg-primary/20 transition-colors">
                Entity Types
              </button>
            </HoverCardTrigger>
            <HoverCardContent className="w-40" side="bottom" align="start">
              <div className="space-y-2">
                <div className="space-y-1.5 max-h-[300px] overflow-y-auto pr-2">
                  {allLabels.map((label) => (
                    <div key={label} className="flex items-center gap-2">
                      <div
                        className="w-4 h-4 rounded-full flex-shrink-0"
                        style={{
                          backgroundColor: getNodeColor(
                            label,
                            isDarkMode,
                            sharedLabelColorMap
                          ),
                        }}
                      />
                      <span className="text-xs">{label}</span>
                    </div>
                  ))}
                </div>
              </div>
            </HoverCardContent>
          </HoverCard>
        </div>

        {triplets.length > 0 ? (
          <Graph
            ref={ref}
            triplets={graphTriplets}
            width={width}
            height={height}
            onNodeClick={handleNodeClick}
            onEdgeClick={handleEdgeClick}
            onBlur={handlePopoverClose}
            zoomOnMount={zoomOnMount}
            labelColorMap={sharedLabelColorMap}
          />
        ) : (
          <div className="flex items-center justify-center h-full">
            <p className="text-muted-foreground">No graph data to visualize.</p>
          </div>
        )}
        <GraphPopovers
          showNodePopup={showNodePopup}
          showEdgePopup={showEdgePopup}
          nodePopupContent={nodePopupContent}
          edgePopupContent={edgePopupContent}
          onOpenChange={handlePopoverClose}
          labelColorMap={sharedLabelColorMap}
        />
      </div>
    );
  }
);
