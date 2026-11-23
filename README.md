# ReelBench

ReelBench is a local inspection bench for digitized film. It keeps the original scan, registers each capture against a measured carrier plane and stores the source and observed support for each result.

Start with `docker compose up --build` and open http://127.0.0.1:19990. The Compose project has a named `reel-library` volume. Close the attached command or run `docker compose stop` to stop the service while retaining scans and results. The local Python entry point is `./start.sh` after installing the pinned requirements for Python 3.11.

## Inspect a scan

1. Load the sample reels or add a capture. Mark the carrier corners and enter its width and height in millimeters. Register alternate captures by matching the same landmarks.
2. In **Sampling regions**, mark the carrier, picture window, optical sound lane and a photographed clear film base as needed. The sample *Harbor crossing · R12* already has these regions.
3. In **Measure**, run **Perforation pitch** on Scanned carrier to see measured openings and interrupted pitches. Run **Frame gate survey** on Picture window for candidate picture bounds. **Optical track diagnostic** reads the marked lane and distinguishes dark dropouts from unobserved columns. **Optical density map** uses the clear-film patch and sensor floor; **Carrier alignment at splice** measures the edges on either side of a join.
4. **Frame registers** turns one or more Frame gate surveys of the same reel into a continuous frame numbering that can be handed to a timing desk. Start a register from a survey, attach the survey of the next scan, join the pictures both scans show, close the overlap, and deliver numbered frames. Delivered numbers keep the scan and position each used, and the table opens an unnumbered or numbered picture back on its adopted scan.
5. Use **Dust and scratch observations**, **Fine scratch network**, **Emulsion texture spacing** and **Line scan** to inspect surface and picture details. **Geometry** measures a marked area or trace. Each saved measurement retains the image map, source and a CSV table. Use **Evidence** to build an archive and **Received evidence** to verify its digests.

## Input and interpretation

Accepts single-frame PNG, JPEG, WebP or TIFF up to 24 MiB and 12 megapixels. A scan record holds up to 10 captures and 24 regions. A registered plane is limited to 1,800 pixels on its longest side. The dedicated film measurements use a horizontal carrier. They retain local support; planar distances do not measure curl. The optical track view is a diagnostic sampled from the supplied image. Optical density is relative to the photographed clear-film reference. The sample scans are synthetic.

A frame register only numbers a picture when its identity is established. Two views may be joined only after matching picture details are marked in both; the details show which pictures repeat across a splice, and their clarity is kept as evidence. Across a splice the register adopts one scan, the one with the higher median picture completeness, with the earlier scan winning a tie within 2%. A short span that reaches unobserved support inside the reel, an unreconciled overlap, or two short spans that may be one picture keeps its number blank, and later numbers wait behind it. Numbers are never derived from perforation pitch or from the distance between holes. Delivering a run records each number with the adopted scan and position, and those positions do not change afterwards.

ReelBench uses the workspace's local image capture, registration, geometry and evidence libraries, with separate carrier, picture, optical-track, density and splice measurement routines.
