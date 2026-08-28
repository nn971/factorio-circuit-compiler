# Milestone C4 coarse macro refinement

C4 refines the dense C3 macro placement before any implementation-level uncoarsening or relay routing.
The retained proof-of-concept must remain circuit-generic and relay-blind.

The coarse objective contains three separately reported terms:

- occupied macro bounding area;
- projected implementation-hypernet HPWL;
- squared logical-net demand across coarse x/y cuts as a congestion estimate.

The annealer may make local macro translations, affinity-directed migrations, swaps, transactional
related-pair translations, and global zoom proposals. Fixed macros remain exact. A configurable hard
area-growth ceiling prevents HPWL improvement from being purchased by simply reopening the compact C3
envelope.

The Snake C4 proof is considered promising when a bounded run reduces C3 projected HPWL materially
while retaining approximately the C3 envelope and completing in seconds. This remains a cheap coarse
criterion: exact relay count, wire reach, occupancy of the final physical blueprint, and transactional
fallback belong to C5 and later acceptance stages.
