import { GraphClient } from "./GraphClient";

export default async function Home() {
  return (
    <div className="flex-1 space-y-4 p-4">
      <div className="flex items-center justify-between space-y-2">
        <h2 className="text-3xl font-bold tracking-tight">
          View Graph Information
        </h2>
      </div>

      <GraphClient />
    </div>
  );
}
