"use client";

import { useState, ChangeEvent, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Share2 } from "lucide-react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { GraphVisualization } from "@/components/graph/GraphVisualization";
import { GraphRef } from "@/components/graph/Graph";
import { RawTriplet } from "@/lib/types/graph";

interface UserDetailsProps {
  userUuid?: string;
}

export function GraphClient({ userUuid: initialUserUuid }: UserDetailsProps) {
  const [isLoadingGraph, setIsLoadingGraph] = useState(false);
  const [triplets, setTriplets] = useState<RawTriplet[]>([]);
  const [graphDialogOpen, setGraphDialogOpen] = useState(false);
  const graphRef = useRef<GraphRef>(null);

  // New state for the user/graph switch and the UUID input
  const [isGraphMode, setIsGraphMode] = useState(false);
  const [entityUuid, setEntityUuid] = useState(initialUserUuid || "");

  const handleLoadGraph = async () => {
    if (!entityUuid.trim()) {
      toast.error("Please enter a UUID");
      return;
    }

    setIsLoadingGraph(true);
    try {
      // Determine the endpoint based on mode
      const endpointType = isGraphMode ? "graph" : "user";
      const response = await fetch(
        `/api/graph/${endpointType}/${encodeURIComponent(entityUuid)}/triplets`
      );

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || `Failed to load ${endpointType} graph`);
      }

      const data = await response.json();
      setTriplets(data.triplets);

      // Open the dialog when graph data is loaded
      setGraphDialogOpen(true);
    } catch (error) {
      console.error("Error loading graph:", error);
      toast.error(
        error instanceof Error ? error.message : "Failed to load graph"
      );
    } finally {
      setIsLoadingGraph(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="grid gap-4 py-4">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
          <div className="flex items-center space-x-2">
            <Switch
              id="mode-switch"
              checked={isGraphMode}
              onCheckedChange={setIsGraphMode}
            />
            <Label htmlFor="mode-switch">
              {isGraphMode ? "Graph Mode" : "User Mode"}
            </Label>
          </div>

          <div className="flex-1 grid gap-2">
            <Label htmlFor="entity-id">
              {isGraphMode ? "Graph UUID" : "User UUID"}
            </Label>
            <Input
              id="entity-id"
              placeholder={
                isGraphMode ? "Enter graph UUID..." : "Enter user UUID..."
              }
              value={entityUuid}
              onChange={(e: ChangeEvent<HTMLInputElement>) =>
                setEntityUuid(e.target.value)
              }
            />
          </div>

          <Button
            variant="default"
            size="lg"
            disabled={isLoadingGraph}
            className="mt-2 sm:mt-0 text-lg font-medium"
            onClick={handleLoadGraph}
          >
            {isLoadingGraph ? (
              "Loading..."
            ) : (
              <>
                <span className="mr-2">View Graph</span>
                <Share2 size={19} />
              </>
            )}
          </Button>
        </div>
      </div>

      {/* Graph Dialog */}
      <Dialog open={graphDialogOpen} onOpenChange={setGraphDialogOpen}>
        <DialogContent className="max-w-none sm:max-w-none md:max-w-none lg:max-w-none w-[100vw] h-[100vh]">
          <DialogHeader>
            <DialogTitle>
              {isGraphMode ? "Graph" : "User"} Relationship Graph
            </DialogTitle>
            <DialogDescription>
              Visualization of {isGraphMode ? "graph" : "user"} relationships
              and connections
            </DialogDescription>
          </DialogHeader>

          <div className="relative flex-1 w-full h-[calc(80vh-8rem)]">
            {triplets.length > 0 && (
              <GraphVisualization
                ref={graphRef}
                triplets={triplets}
                width={window.innerWidth}
                height={window.innerHeight * 0.75}
                zoomOnMount={true}
              />
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setGraphDialogOpen(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
