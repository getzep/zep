"use client";

import {
  useEffect,
  useRef,
  useMemo,
  useCallback,
  useImperativeHandle,
  forwardRef,
} from "react";
import * as d3 from "d3";
import colors from "tailwindcss/colors";
import { useTheme } from "next-themes";
import type { GraphTriplet, IdValue, GraphNode } from "@/lib/types/graph";
import {
  createLabelColorMap,
  getNodeColor as getNodeColorByLabel,
} from "@/lib/utils/nodeColors";

interface GraphProps {
  triplets: GraphTriplet[];
  width?: number;
  height?: number;
  zoomOnMount?: boolean;
  onNodeClick?: (nodeId: string) => void;
  onEdgeClick?: (edgeId: string) => void;
  onBlur?: () => void;
  labelColorMap?: Map<string, number>;
}

// Add ref type for zoomToLinkById
export interface GraphRef {
  zoomToLinkById: (linkId: string) => void;
}

// eslint-disable-next-line react/display-name
export const Graph = forwardRef<GraphRef, GraphProps>(
  (
    {
      triplets,
      width = 1000,
      height = 800,
      zoomOnMount = true,
      onNodeClick,
      onEdgeClick,
      onBlur,
      labelColorMap: externalLabelColorMap,
    },
    ref
  ) => {
    const svgRef = useRef<SVGSVGElement>(null);
    const { resolvedTheme: themeMode } = useTheme();

    // Function refs to keep track of reset functions
    const resetLinksRef = useRef<(() => void) | null>(null);
    const resetNodesRef = useRef<(() => void) | null>(null);
    const handleLinkClickRef = useRef<
      ((event: any, d: any, relation: IdValue) => void) | null
    >(null);
    const simulationRef = useRef<d3.Simulation<
      d3.SimulationNodeDatum,
      undefined
    > | null>(null);
    const zoomRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(
      null
    );
    const isInitializedRef = useRef(false);

    // Add ref for zoomToLinkById
    const graphRef = useRef<GraphRef>({
      zoomToLinkById: (linkId: string) => {
        if (
          !svgRef.current ||
          !resetLinksRef.current ||
          !resetNodesRef.current ||
          !handleLinkClickRef.current
        )
          return;
        const svgElement = d3.select(svgRef.current);
        const linkGroups = svgElement.selectAll("g > g"); // Select all link groups

        let found = false;

        // Iterate through link groups to find matching relation
        linkGroups.each(function (d: any) {
          if (found) return; // Skip if already found

          if (d?.relationData) {
            const relation = d.relationData.find(
              (r: IdValue) => r.id === linkId
            );
            if (relation) {
              found = true;
              const resetLinks = resetLinksRef.current;
              const resetNodes = resetNodesRef.current;
              const handleLinkClick = handleLinkClickRef.current;

              if (resetLinks) resetLinks();
              if (resetNodes) resetNodes();
              if (handleLinkClick)
                handleLinkClick({ stopPropagation: () => {} }, d, relation);
            }
          }
        });

        if (!found) {
          console.warn(`Link with id ${linkId} not found`);
        }
      },
    });

    // Expose the ref through forwardRef
    useImperativeHandle(ref, () => graphRef.current);

    // Memoize theme to prevent unnecessary recreation
    const theme = useMemo(
      () => ({
        node: {
          fill: colors.pink[500],
          stroke: themeMode === "dark" ? colors.slate[100] : colors.slate[900],
          hover: colors.blue[400],
          text: themeMode === "dark" ? colors.slate[100] : colors.slate[900],
          selected: colors.blue[500],
          dimmed: colors.pink[300],
        },
        link: {
          stroke: themeMode === "dark" ? colors.slate[600] : colors.slate[400],
          selected: colors.blue[400],
          dimmed: themeMode === "dark" ? colors.slate[800] : colors.slate[200],
          label: {
            bg: themeMode === "dark" ? colors.slate[800] : colors.slate[200],
            text: themeMode === "dark" ? colors.slate[100] : colors.slate[900],
          },
        },
        background:
          themeMode === "dark" ? colors.slate[900] : colors.slate[100],
        controls: {
          bg: themeMode === "dark" ? colors.slate[800] : colors.slate[200],
          hover: themeMode === "dark" ? colors.slate[700] : colors.slate[300],
          text: themeMode === "dark" ? colors.slate[100] : colors.slate[900],
        },
      }),
      [themeMode]
    );

    // Extract all unique labels from triplets
    const allLabels = useMemo(() => {
      // Only calculate if we need to create our own map
      if (externalLabelColorMap) return [];

      const labels = new Set<string>();
      labels.add("Entity"); // Always include Entity as default

      triplets.forEach((triplet) => {
        if (triplet.source.primaryLabel)
          labels.add(triplet.source.primaryLabel);
        if (triplet.target.primaryLabel)
          labels.add(triplet.target.primaryLabel);
      });

      return Array.from(labels);
    }, [triplets, externalLabelColorMap]);

    // Create a mapping of label to color
    const labelColorMap = useMemo(() => {
      return externalLabelColorMap || createLabelColorMap(allLabels);
    }, [allLabels, externalLabelColorMap]);

    // Create a mapping of node IDs to their data
    const nodeDataMap = useMemo(() => {
      const result = new Map<string, GraphNode>();

      triplets.forEach((triplet) => {
        result.set(triplet.source.id, triplet.source);
        result.set(triplet.target.id, triplet.target);
      });

      return result;
    }, [triplets]);

    // Function to get node color
    const getNodeColor = useCallback(
      (node: any): string => {
        if (!node) {
          return getNodeColorByLabel(null, themeMode === "dark", labelColorMap);
        }

        // Get the full node data if we only have an ID
        const nodeData = nodeDataMap.get(node.id) || node;

        // Extract primaryLabel from node data
        const primaryLabel = nodeData.primaryLabel;

        return getNodeColorByLabel(
          primaryLabel,
          themeMode === "dark",
          labelColorMap
        );
      },
      [labelColorMap, nodeDataMap, themeMode]
    );

    // Process graph data
    const { nodes, links } = useMemo(() => {
      const nodes = Array.from(
        new Set(triplets.flatMap((t) => [t.source.id, t.target.id]))
      ).map((id) => {
        const nodeData = triplets.find(
          (t) => t.source.id === id || t.target.id === id
        );
        const value = nodeData
          ? nodeData.source.id === id
            ? nodeData.source.value
            : nodeData.target.value
          : id;
        return {
          id,
          value,
        };
      });

      const linkGroups = triplets.reduce(
        (groups, triplet) => {
          // Skip isolated node edges (they are just placeholders for showing isolated nodes)
          if (triplet.relation.type === "_isolated_node_") {
            return groups;
          }

          let key = `${triplet.source.id}-${triplet.target.id}`;
          const reverseKey = `${triplet.target.id}-${triplet.source.id}`;

          if (groups[reverseKey]) {
            key = reverseKey;
          }

          if (!groups[key]) {
            groups[key] = {
              source: triplet.source.id,
              target: triplet.target.id,
              relations: [],
              relationData: [],
              curveStrength: 0,
            };
          }
          groups[key].relations.push(triplet.relation.value);
          groups[key].relationData.push(triplet.relation);
          return groups;
        },
        {} as Record<
          string,
          {
            source: string;
            target: string;
            relations: string[];
            relationData: IdValue[];
            curveStrength: number;
          }
        >
      );

      return {
        nodes,
        links: Object.values(linkGroups),
      };
    }, [triplets]);

    // Initialize or update visualization - This will run only once on mount
    useEffect(() => {
      // Skip if already initialized or ref not available
      if (isInitializedRef.current || !svgRef.current) return;

      // Mark as initialized to prevent re-running
      isInitializedRef.current = true;

      const svgElement = d3.select<SVGSVGElement, unknown>(svgRef.current);
      svgElement.selectAll("*").remove();

      const g = svgElement.append("g");

      // Drag handler function
      const drag = (
        simulation: d3.Simulation<d3.SimulationNodeDatum, undefined>
      ) => {
        const originalSettings = {
          velocityDecay: 0.4,
          alphaDecay: 0.05,
        };

        function dragstarted(event: any) {
          if (!event.active) {
            simulation
              .velocityDecay(0.7)
              .alphaDecay(0.1)
              .alphaTarget(0.1)
              .restart();
          }
          d3.select(event.sourceEvent.target.parentNode)
            .select("circle")
            .attr("stroke", theme.node.hover)
            .attr("stroke-width", 3);

          event.subject.fx = event.subject.x;
          event.subject.fy = event.subject.y;
        }

        function dragged(event: any) {
          event.subject.x = event.x;
          event.subject.y = event.y;
          event.subject.fx = event.x;
          event.subject.fy = event.y;
        }

        function dragended(event: any) {
          if (!event.active) {
            simulation
              .velocityDecay(originalSettings.velocityDecay)
              .alphaDecay(originalSettings.alphaDecay)
              .alphaTarget(0);
          }

          // Keep the node fixed at its final position
          event.subject.fx = event.x;
          event.subject.fy = event.y;

          d3.select(event.sourceEvent.target.parentNode)
            .select("circle")
            .attr("stroke", theme.node.stroke)
            .attr("stroke-width", 2);
        }

        return d3
          .drag<any, any>()
          .on("start", dragstarted)
          .on("drag", dragged)
          .on("end", dragended);
      };

      // Setup zoom behavior
      const zoom = d3
        .zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.1, 4])
        .on("zoom", (event) => {
          g.attr("transform", event.transform);
        });

      zoomRef.current = zoom;
      // @ts-ignore
      svgElement.call(zoom).call(zoom.transform, d3.zoomIdentity.scale(0.8));

      // Identify which nodes are isolated (not in any links)
      const nodeIdSet = new Set(nodes.map((n: any) => n.id));
      const linkedNodeIds = new Set<string>();

      links.forEach((link: any) => {
        const sourceId =
          typeof link.source === "string" ? link.source : link.source.id;
        const targetId =
          typeof link.target === "string" ? link.target : link.target.id;
        linkedNodeIds.add(sourceId);
        linkedNodeIds.add(targetId);
      });

      // Nodes that don't appear in any link are isolated
      const isolatedNodeIds = new Set<string>();
      nodeIdSet.forEach((nodeId: string) => {
        if (!linkedNodeIds.has(nodeId)) {
          isolatedNodeIds.add(nodeId);
        }
      });

      // Create simulation with custom forces
      const simulation = d3
        .forceSimulation(nodes as d3.SimulationNodeDatum[])
        .force(
          "link",
          d3
            .forceLink(links)
            .id((d: any) => d.id)
            .distance(200)
            .strength(0.2)
        )
        .force(
          "charge",
          d3
            .forceManyBody()
            .strength((d: any) => {
              // Use a less negative strength for isolated nodes
              // to pull them closer to the center
              return isolatedNodeIds.has(d.id) ? -500 : -3000;
            })
            .distanceMin(20)
            .distanceMax(500)
            .theta(0.8)
        )
        .force("center", d3.forceCenter(width / 2, height / 2).strength(0.05))
        .force(
          "collide",
          d3.forceCollide().radius(50).strength(0.3).iterations(5)
        )
        // Add a special gravity force for isolated nodes to pull them toward the center
        .force(
          "isolatedGravity",
          d3
            .forceRadial(
              100, // distance from center
              width / 2, // center x
              height / 2 // center y
            )
            .strength((d: any) => (isolatedNodeIds.has(d.id) ? 0.15 : 0.01))
        )
        .velocityDecay(0.4)
        .alphaDecay(0.05)
        .alphaMin(0.001);

      simulationRef.current = simulation;

      const link = g.append("g").selectAll("g").data(links).join("g");

      // Define reset functions
      resetLinksRef.current = () => {
        // @ts-ignore
        link
          .selectAll("path")
          .attr("stroke", theme.link.stroke)
          .attr("stroke-opacity", 0.6)
          .attr("stroke-width", 1);

        // @ts-ignore
        link.selectAll(".link-label rect").attr("fill", theme.link.label.bg);
        // @ts-ignore
        link.selectAll(".link-label text").attr("fill", theme.link.label.text);
      };

      // Create node groups
      const node = g
        .append("g")
        .selectAll("g")
        .data(nodes)
        .join("g")
        // @ts-ignore
        .call(drag(simulation))
        .attr("cursor", "pointer");

      resetNodesRef.current = () => {
        // @ts-ignore
        node
          .selectAll("circle")
          .attr("fill", (d: any) => getNodeColor(d))
          .attr("stroke", theme.node.stroke)
          .attr("stroke-width", 1);
      };

      // Handle link click
      handleLinkClickRef.current = (event: any, d: any, relation: IdValue) => {
        if (event.stopPropagation) {
          event.stopPropagation();
        }

        if (resetLinksRef.current) resetLinksRef.current();
        if (onEdgeClick) onEdgeClick(relation.id);

        // Reset all elements to default state
        // @ts-ignore
        link
          .selectAll("path")
          .attr("stroke", theme.link.stroke)
          .attr("stroke-opacity", 0.6)
          .attr("stroke-width", 1);

        // Reset non-highlighted nodes to their proper colors
        // @ts-ignore
        node
          .selectAll("circle")
          .attr("fill", (d: any) => getNodeColor(d))
          .attr("stroke", theme.node.stroke)
          .attr("stroke-width", 1);

        // Find and highlight the corresponding path and label
        const linkGroup = event.target?.closest("g")
          ? d3.select(event.target.closest("g"))
          : link.filter((l: any) => l === d);

        // @ts-ignore
        linkGroup
          // @ts-ignore
          .selectAll("path")
          .attr("stroke", theme.link.selected)
          .attr("stroke-opacity", 1)
          .attr("stroke-width", 2);

        // Update label styling
        // @ts-ignore
        linkGroup.select(".link-label rect").attr("fill", theme.link.selected);
        // @ts-ignore
        linkGroup.select(".link-label text").attr("fill", theme.node.text);

        // Highlight connected nodes
        // @ts-ignore
        node
          .selectAll("circle")
          .filter((n: any) => n.id === d.source.id || n.id === d.target.id)
          .attr("fill", theme.node.selected)
          .attr("stroke", theme.node.selected)
          .attr("stroke-width", 2);

        const sourceNode = d.source;
        const targetNode = d.target;

        // Calculate bounding box for the two connected nodes and the edge
        if (
          sourceNode &&
          targetNode &&
          sourceNode.x !== undefined &&
          targetNode.x !== undefined
        ) {
          const padding = 100; // Increased padding for better view
          const minX = Math.min(sourceNode.x, targetNode.x) - padding;
          const minY = Math.min(sourceNode.y, targetNode.y) - padding;
          const maxX = Math.max(sourceNode.x, targetNode.x) + padding;
          const maxY = Math.max(sourceNode.y, targetNode.y) + padding;

          // Calculate transform to fit the connected nodes
          const boundWidth = maxX - minX;
          const boundHeight = maxY - minY;
          const scale =
            0.9 * Math.min(width / boundWidth, height / boundHeight);
          const midX = (minX + maxX) / 2;
          const midY = (minY + maxY) / 2;

          if (
            isFinite(scale) &&
            isFinite(midX) &&
            isFinite(midY) &&
            zoomRef.current
          ) {
            const transform = d3.zoomIdentity
              .translate(width / 2 - midX * scale, height / 2 - midY * scale)
              .scale(scale);

            // Animate transition to new view
            // @ts-ignore
            svgElement
              .transition()
              .duration(750)
              .ease(d3.easeCubicInOut) // Add easing for smoother transitions
              .call(zoomRef.current.transform, transform);
          }
        }
      };

      // Create links with proper curve paths
      link.each(function (d: any) {
        const linkGroup = d3.select(this);
        const relationCount = d.relations.length;

        // Calculate curve strengths based on number of relations
        const baseStrength = 0.2;
        const strengthStep =
          relationCount > 1 ? baseStrength / (relationCount - 1) : 0;

        d.relations.forEach((relation: string, index: number) => {
          const curveStrength =
            relationCount > 1 ? -baseStrength + index * strengthStep * 2 : 0;
          const fullRelation = d.relationData[index];

          linkGroup
            .append("path")
            .attr("stroke", theme.link.stroke)
            .attr("stroke-opacity", 0.6)
            .attr("stroke-width", 1)
            .attr("fill", "none")
            .attr("data-curve-strength", curveStrength)
            .attr("cursor", "pointer")
            .attr(
              "data-source",
              typeof d.source === "object" ? d.source.id : d.source
            )
            .attr(
              "data-target",
              typeof d.target === "object" ? d.target.id : d.target
            )
            .on("click", (event) => {
              if (handleLinkClickRef.current) {
                handleLinkClickRef.current(event, d, fullRelation);
              }
            });

          const labelGroup = linkGroup
            .append("g")
            .attr("class", "link-label")
            .attr("cursor", "pointer")
            .attr("data-curve-strength", curveStrength)
            .on("click", (event) => {
              if (handleLinkClickRef.current) {
                handleLinkClickRef.current(event, d, fullRelation);
              }
            });

          labelGroup
            .append("rect")
            .attr("fill", theme.link.label.bg)
            .attr("rx", 4)
            .attr("ry", 4)
            .attr("opacity", 0.9);

          labelGroup
            .append("text")
            .attr("fill", theme.link.label.text)
            .attr("font-size", "8px")
            .attr("text-anchor", "middle")
            .attr("dominant-baseline", "middle")
            .attr("pointer-events", "none")
            .text(relation);

          labelGroup.attr("data-curve-strength", curveStrength);
        });
      });

      // Create node circles
      node
        .append("circle")
        .attr("r", 10)
        .attr("fill", (d: any) => getNodeColor(d))
        .attr("stroke", theme.node.stroke)
        .attr("stroke-width", 1)
        .attr("filter", "drop-shadow(0 2px 4px rgba(0,0,0,0.2))")
        .attr("data-id", (d: any) => d.id)
        .attr("cursor", "pointer");

      // Add node labels
      node
        .append("text")
        .attr("x", 15)
        .attr("y", "0.3em")
        .attr("text-anchor", "start")
        .attr("fill", theme.node.text)
        .attr("font-weight", "500")
        .attr("font-size", "12px")
        .text((d: any) => d.value)
        .attr("cursor", "pointer");

      // Handle node clicks
      function handleNodeClick(event: any, d: any) {
        event.stopPropagation(); // Ensure the event doesn't bubble up

        if (resetLinksRef.current) resetLinksRef.current();
        if (resetNodesRef.current) resetNodesRef.current();

        const selectedNodeId = d?.id;

        if (selectedNodeId && onNodeClick) {
          onNodeClick(selectedNodeId);

          // Highlight the selected node
          // @ts-ignore
          node
            .selectAll("circle")
            .filter((n: any) => n.id === selectedNodeId)
            .attr("fill", theme.node.selected)
            .attr("stroke", theme.node.selected)
            .attr("stroke-width", 2);

          // Find connected nodes and links
          const connectedLinks: any[] = [];
          const connectedNodes = new Set<any>();

          // Add the selected node to the connected nodes
          const selectedNode = nodes.find((n: any) => n.id === selectedNodeId);
          if (selectedNode) {
            connectedNodes.add(selectedNode);
          }

          // @ts-ignore
          link.selectAll("path").each(function () {
            const path = d3.select(this);
            const source = path.attr("data-source");
            const target = path.attr("data-target");

            if (source === selectedNodeId || target === selectedNodeId) {
              const sourceNode = nodes.find((n: any) => n.id === source);
              const targetNode = nodes.find((n: any) => n.id === target);

              if (sourceNode && targetNode) {
                connectedLinks.push({ source: sourceNode, target: targetNode });
                connectedNodes.add(sourceNode);
                connectedNodes.add(targetNode);
              }
            }
          });

          // Calculate bounding box of connected nodes
          if (connectedNodes.size > 0 && zoomRef.current) {
            let minX = Infinity,
              minY = Infinity;
            let maxX = -Infinity,
              maxY = -Infinity;

            connectedNodes.forEach((node: any) => {
              if (node.x !== undefined && node.y !== undefined) {
                minX = Math.min(minX, node.x);
                minY = Math.min(minY, node.y);
                maxX = Math.max(maxX, node.x);
                maxY = Math.max(maxY, node.y);
              }
            });

            // Add padding
            const padding = 50;
            minX -= padding;
            minY -= padding;
            maxX += padding;
            maxY += padding;

            // Calculate transform to fit connected nodes
            const boundWidth = maxX - minX;
            const boundHeight = maxY - minY;
            const scale =
              0.9 * Math.min(width / boundWidth, height / boundHeight);
            const midX = (minX + maxX) / 2;
            const midY = (minY + maxY) / 2;

            if (isFinite(scale) && isFinite(midX) && isFinite(midY)) {
              const transform = d3.zoomIdentity
                .translate(width / 2 - midX * scale, height / 2 - midY * scale)
                .scale(scale);

              // Animate transition to new view
              // @ts-ignore
              svgElement
                .transition()
                .duration(750)
                .ease(d3.easeCubicInOut) // Add easing for smoother transitions
                .call(zoomRef.current.transform, transform);
            }
          }

          // Highlight connected links
          // @ts-ignore
          link
            .selectAll("path")
            .attr("stroke", theme.link.stroke)
            .attr("stroke-opacity", 0.6)
            .attr("stroke-width", 1)
            .filter(function () {
              const path = d3.select(this);
              return (
                path.attr("data-source") === selectedNodeId ||
                path.attr("data-target") === selectedNodeId
              );
            })
            .attr("stroke", themeMode === "dark" ? "#ffffff" : colors.pink[600])
            .attr("stroke-width", 2);
        }
      }

      // Attach click handler to nodes
      node.on("click", handleNodeClick);

      // Store a reference to the current SVG element
      const svgRefCurrent = svgRef.current;

      // Add blur handler
      svgElement.on("click", function (event) {
        // Make sure we only handle clicks directly on the SVG element, not on its children
        if (event.target === svgRefCurrent) {
          if (onBlur) onBlur();
          if (resetLinksRef.current) resetLinksRef.current();
          if (resetNodesRef.current) resetNodesRef.current();
        }
      });

      // Update positions on simulation tick
      simulation.on("tick", () => {
        // Update link paths and labels
        link.each(function (d: any) {
          // Make sure d.source and d.target have x and y properties
          if (!d.source.x && typeof d.source === "string") {
            const sourceNode = nodes.find((n: any) => n.id === d.source);
            // @ts-ignore - Node will have x,y properties from d3 simulation
            if (sourceNode && sourceNode.x) {
              d.source = sourceNode;
            }
          }

          if (!d.target.x && typeof d.target === "string") {
            const targetNode = nodes.find((n: any) => n.id === d.target);
            // @ts-ignore - Node will have x,y properties from d3 simulation
            if (targetNode && targetNode.x) {
              d.target = targetNode;
            }
          }

          const linkGroup = d3.select(this);
          linkGroup.selectAll("path").each(function () {
            const path = d3.select(this);
            const curveStrength = +path.attr("data-curve-strength") || 0;

            // Handle self-referencing nodes
            if (d.source.id === d.target.id) {
              // Create an elliptical path for self-references
              const radiusX = 40;
              const radiusY = 90;
              const offset = radiusY + 20;

              const cx = d.source.x;
              const cy = d.source.y - offset;
              const path_d = `M${d.source.x},${d.source.y} 
              C${cx - radiusX},${cy} 
               ${cx + radiusX},${cy} 
               ${d.source.x},${d.source.y}`;
              path.attr("d", path_d);

              // Position the label
              // @ts-ignore
              const labelGroup = linkGroup
                .selectAll(".link-label")
                .filter(function () {
                  return (
                    d3.select(this).attr("data-curve-strength") ===
                    String(curveStrength)
                  );
                });

              // Update both the group position and the rectangle/text within it
              labelGroup.attr("transform", `translate(${cx}, ${cy - 10})`);

              // Update the rectangle and text positioning
              // @ts-ignore
              const text = labelGroup.select("text");
              // @ts-ignore
              const rect = labelGroup.select("rect");
              const textBBox = (text.node() as SVGTextElement)?.getBBox();

              if (textBBox) {
                rect
                  .attr("x", -textBBox.width / 2 - 6)
                  .attr("y", -textBBox.height / 2 - 4)
                  .attr("width", textBBox.width + 12)
                  .attr("height", textBBox.height + 8);

                text.attr("x", 0).attr("y", 0);
              }
            } else {
              const dx = d.target.x - d.source.x;
              const dy = d.target.y - d.source.y;
              const dr = Math.sqrt(dx * dx + dy * dy);

              const midX = (d.source.x + d.target.x) / 2;
              const midY = (d.source.y + d.target.y) / 2;
              const normalX = -dy / dr;
              const normalY = dx / dr;
              const curveMagnitude = dr * curveStrength;
              const controlX = midX + normalX * curveMagnitude;
              const controlY = midY + normalY * curveMagnitude;

              const path_d = `M${d.source.x},${d.source.y} Q${controlX},${controlY} ${d.target.x},${d.target.y}`;
              path.attr("d", path_d);

              const pathNode = path.node() as SVGPathElement;
              if (pathNode) {
                const pathLength = pathNode.getTotalLength();
                const midPoint = pathNode.getPointAtLength(pathLength / 2);

                // @ts-ignore - Intentionally ignoring d3 selection type issues as in the Svelte version
                const labelGroup = linkGroup
                  .selectAll(".link-label")
                  .filter(function () {
                    return (
                      d3.select(this).attr("data-curve-strength") ===
                      String(curveStrength)
                    );
                  });

                if (midPoint) {
                  // @ts-ignore - Intentionally ignoring d3 selection type issues as in the Svelte version
                  const text = labelGroup.select("text");
                  // @ts-ignore - Intentionally ignoring d3 selection type issues as in the Svelte version
                  const rect = labelGroup.select("rect");
                  const textBBox = (text.node() as SVGTextElement)?.getBBox();

                  if (textBBox) {
                    const angle =
                      (Math.atan2(
                        d.target.y - d.source.y,
                        d.target.x - d.source.x
                      ) *
                        180) /
                      Math.PI;
                    const rotationAngle =
                      angle > 90 || angle < -90 ? angle - 180 : angle;

                    labelGroup.attr(
                      "transform",
                      `translate(${midPoint.x}, ${midPoint.y}) rotate(${rotationAngle})`
                    );

                    rect
                      .attr("x", -textBBox.width / 2 - 6)
                      .attr("y", -textBBox.height / 2 - 4)
                      .attr("width", textBBox.width + 12)
                      .attr("height", textBBox.height + 8);

                    text.attr("x", 0).attr("y", 0);
                  }
                }
              }
            }
          });
        });

        // Update node positions
        node.attr("transform", (d: any) => `translate(${d.x},${d.y})`);
      });

      // Handle zoom-to-fit on mount
      let hasInitialized = false;

      simulation.on("end", () => {
        if (hasInitialized || !zoomOnMount || !zoomRef.current) return;
        hasInitialized = true;

        const bounds = g.node()?.getBBox();
        if (bounds) {
          const fullWidth = width;
          const fullHeight = height;
          const currentWidth = bounds.width || 1;
          const currentHeight = bounds.height || 1;

          // Only proceed if we have valid dimensions
          if (
            currentWidth > 0 &&
            currentHeight > 0 &&
            fullWidth > 0 &&
            fullHeight > 0
          ) {
            const midX = bounds.x + currentWidth / 2;
            const midY = bounds.y + currentHeight / 2;

            // Calculate scale to fit with padding
            const scale =
              0.8 *
              Math.min(fullWidth / currentWidth, fullHeight / currentHeight);

            // Ensure we have valid numbers before creating transform
            if (isFinite(midX) && isFinite(midY) && isFinite(scale)) {
              const transform = d3.zoomIdentity
                .translate(
                  fullWidth / 2 - midX * scale,
                  fullHeight / 2 - midY * scale
                )
                .scale(scale);

              // Smoothly animate to the new transform
              // @ts-ignore
              svgElement
                .transition()
                .duration(750)
                .ease(d3.easeCubicInOut) // Add easing for smoother transitions
                .call(zoomRef.current.transform, transform);
            } else {
              console.warn("Invalid transform values:", { midX, midY, scale });
              // Fallback to a simple center transform
              const transform = d3.zoomIdentity
                .translate(fullWidth / 2, fullHeight / 2)
                .scale(0.8);
              svgElement.call(zoomRef.current.transform, transform);
            }
          }
        }
      });

      // Cleanup function - only called when component unmounts
      return () => {
        simulation.stop();
        // Save the ref to a variable before using it in cleanup
        const currentSvgRef = svgRef.current;
        if (currentSvgRef) {
          d3.select(currentSvgRef).on("click", null);
        }
        isInitializedRef.current = false;
      };
      // We're keeping the dependency array empty to ensure initialization runs only once on mount
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // This effect updates the graph theme colors when the theme changes
    useEffect(() => {
      // Skip if not initialized
      if (!svgRef.current || !isInitializedRef.current) return;

      const svgElement = d3.select(svgRef.current);

      // Update background
      svgElement.style("background-color", theme.background);

      // Update nodes - use getNodeColor for proper color assignment
      svgElement
        .selectAll("circle")
        .attr("fill", (d: any) => getNodeColor(d))
        .attr("stroke", theme.node.stroke);

      // Update node labels
      // @ts-ignore
      svgElement.selectAll("text").attr("fill", theme.node.text);

      // Update links
      // @ts-ignore
      svgElement
        .selectAll("path")
        .attr("stroke", theme.link.stroke)
        .attr("stroke-opacity", 0.6);

      // Update selected links if any
      // @ts-ignore
      svgElement
        .selectAll("path.selected")
        .attr("stroke", theme.link.selected)
        .attr("stroke-opacity", 1);

      // Update link labels
      // @ts-ignore
      svgElement
        .selectAll(".link-label rect")
        .attr("fill", theme.link.label.bg);
      // @ts-ignore
      svgElement
        .selectAll(".link-label text")
        .attr("fill", theme.link.label.text);

      // This effect has many dependencies that would cause frequent re-renders
      // We're disabling the exhaustive deps rule to prevent unnecessary re-renders
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [themeMode]);

    return (
      <svg
        ref={svgRef}
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        style={{
          width: "100%",
          height: `${height}px`,
          backgroundColor: theme.background,
          borderRadius: "8px",
          cursor: "grab",
        }}
      />
    );
  }
);
