export const number = (v: number) =>
  new Intl.NumberFormat("en", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(v);
export const bytes = (v: number) =>
  v > 1048576
    ? `${(v / 1048576).toFixed(1)} MB`
    : `${(v / 1024).toFixed(1)} KB`;
export const time = (v: string) => new Date(v).toLocaleString();

export function datasetLabel(dataset: {
  name: string;
  display_name?: string | null;
}) {
  return dataset.name || dataset.display_name || "table";
}
