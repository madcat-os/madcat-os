-- v2 deletes a memory's edges together with the memory. Edges left behind by earlier servers,
-- which forgot memories without touching edges, point at nothing; drop them.
DELETE FROM edges e
WHERE NOT EXISTS (SELECT 1 FROM memories m WHERE m.id = e.from_id)
   OR NOT EXISTS (SELECT 1 FROM memories m WHERE m.id = e.to_id);
