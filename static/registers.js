import {e, btn, badge, img, n, date, table, link} from "./widgets.js";


function stateBadge(entry) {
  if (entry.state === "sealed") return badge(`Delivered · #${entry.number}`, "ready");
  if (entry.state === "numbered") return badge(`Frame ${entry.number}`, "ready");
  return badge("Number blank", "caution");
}

function entryById(reg, id) {
  return reg.active_entries.find(item => item.id === id) || null;
}

function entries(reg) {
  return `<div class="table-wrap"><table><thead><tr>
      <th>Frame</th><th>State</th><th>Views</th><th>Adopted image</th><th>Completeness</th><th></th>
    </tr></thead><tbody>${reg.active_entries.map(entry => {
      const adopted = entry.adopted || {};
      const views = entry.observations
        .map(item => `${e(item.scan_name)} · gate ${item.gate_index}`)
        .join("<br>");
      return `<tr>
        <td><strong>${entry.number == null ? "—" : "#" + entry.number}</strong></td>
        <td>${stateBadge(entry)}${entry.suspect ? " " + badge("Broken edge?", "caution") : ""}</td>
        <td>${views || "—"}</td>
        <td>${e(adopted.photo_name || "—")}${adopted.gate_index ? ` · gate ${adopted.gate_index}` : ""}<br><small>${e(adopted.condition || "")}</small></td>
        <td>${adopted.completeness == null ? "—" : n(adopted.completeness, 3)}</td>
        <td>${btn("Open", "register-entry", "tiny secondary", null, `data-id="${entry.id}"`)}</td>
      </tr>`;
    }).join("")}</tbody></table></div>`;
}

export function entryPanel(reg, entry) {
  if (!entry) {
    return `<section class="card stack"><h3>Picture detail</h3><p class="soft">Open a row to see the adopted picture and its place on the original scan.</p></section>`;
  }
  const adopted = entry.adopted || {};
  const segment = reg.segments.find(item => item.id === adopted.segment_id);
  const position = reg.active_entries.findIndex(item => item.id === entry.id) + 1;
  const detail = [
    ["Frame number", entry.number == null ? "blank" : `#${entry.number}`],
    ["Position in register", `${position} of ${reg.active_entries.length}`],
    ["State", entry.state],
    ["Decision", entry.decision],
    ["Adopted scan", adopted.photo_name || "—"],
    ["Adopted picture", adopted.gate_index == null ? "—" : `gate ${adopted.gate_index}`],
    ["Completeness", adopted.completeness == null ? "—" : n(adopted.completeness, 3)],
    ["Why this scan was adopted", entry.adoption_reason || "—"],
    ["Why the number is blank", entry.reason || "—"],
  ];
  const views = entry.observations
    .map(item => `<li><strong>${e(item.scan_name)}</strong> · gate ${item.gate_index} · ${e(item.condition)} · completeness ${n(item.completeness, 3)}</li>`)
    .join("");
  const evidence = entry.evidence || {};
  const marks = (evidence.landmarks || [])
    .map(item => `<li>${e(item.detail || "matching detail")} · earlier ${item.first.map(value => n(value, 1)).join(", ")} · later ${item.second.map(value => n(value, 1)).join(", ")}</li>`)
    .join("");
  return `<section class="card stack">
    <div class="title-row"><div><h3>${entry.number == null ? "Unnumbered picture" : `Frame ${entry.number}`}</h3><small>${e(adopted.photo_name || "")}</small></div>${stateBadge(entry)}</div>
    <figure class="stage">${img(entry.frame_url, `Adopted picture ${entry.number == null ? "without a number" : entry.number}`)}</figure>
    <figure class="stage">${img(entry.scan_url, "Adopted scan with the picture outlined")}</figure>
    <p class="stage-foot">The second view is the adopted scan with this picture outlined. ${segment ? btn("Open the registered scan", "register-scan", "tiny secondary", "frame", `data-specimen="${segment.specimen_id}" data-photo="${adopted.photo_id || ""}"`) : ""}</p>
    ${table(["Property", "Value"], detail)}
    <details><summary>Views of this picture</summary><ul>${views}</ul></details>
    ${marks ? `<details><summary>Matching picture details</summary><ul>${marks}</ul></details>` : ""}
    <div class="actions">
      ${entry.resolved ? "" : btn("Record as a whole picture", "register-whole", "secondary", "check", `data-id="${entry.id}"`)}
      ${entry.decision != "open" ? btn("Clear decision", "register-undo-decision", "ghost", null, `data-id="${entry.id}"`) : ""}
    </div>
    ${entry.resolved ? "" : `<p class="soft">Only record a whole picture when the scan itself shows both ends, or when this is the outer end of the reel.</p>`}
  </section>`;
}

function suspectPanel(reg) {
  const pairs = reg.suspects || [];
  if (!pairs.length) return "";
  return `<section class="card stack"><h3>Suspected broken-edge pairs</h3>
    ${pairs.map(pair => `<div class="row"><div class="grow"><strong>${e(pair.scan_name)} · gates ${pair.first_gate} and ${pair.second_gate}</strong><br><small>${e(pair.reason)}</small></div>
      ${btn("These are one picture", "register-torn-join", "tiny secondary", "compare", `data-a="${pair.entry_ids[0]}" data-b="${pair.entry_ids[1]}"`)}
      ${btn("These are separate", "register-separate", "tiny ghost", null, `data-a="${pair.entry_ids[0]}" data-b="${pair.entry_ids[1]}"`)}</div>`).join("")}
  </section>`;
}

function junctionPanel(reg) {
  if (!reg.junctions.length) return "";
  return reg.junctions.map(junction => {
    const earlier = reg.segments.find(item => item.id === junction.earlier_segment_id);
    const later = reg.segments.find(item => item.id === junction.later_segment_id);
    const adopted = reg.segments.find(item => item.id === junction.adopted_segment_id);
    const open = junction.status === "open";
    const anchor = entryById(reg, junction.anchor_entry_id);
    return `<section class="card stack">
      <div class="title-row"><div><h3>${e(earlier?.photo.name || "Scan")} → ${e(later?.photo.name || "Scan")}</h3>
        <small>Overlap at the splice</small></div>${open ? badge("Needs reconciliation", "caution") : badge(junction.status === "adjacent" ? "Adjacent · no repeated picture" : "Repeated pictures joined", "ready")}</div>
      <div class="metrics">
        <div class="stat"><strong>${n(junction.completeness?.[junction.earlier_segment_id], 3)}</strong><span>${e(earlier?.photo.name || "Earlier scan")} median completeness</span></div>
        <div class="stat"><strong>${n(junction.completeness?.[junction.later_segment_id], 3)}</strong><span>${e(later?.photo.name || "Later scan")} median completeness</span></div>
        <div class="stat"><strong>${e(adopted?.photo.name || "—")}</strong><span>Adopted scan</span></div>
      </div>
      ${junction.adoption_reason ? `<p class="notice">${e(junction.adoption_reason)}</p>` : ""}
      ${open ? `<p class="soft">Pair each picture both scans show, then record the last repeated picture and close the overlap. Pictures left unpaired keep blank numbers.</p>
        <div class="form-columns">
          <div><h4>Earlier scan · last pictures</h4><div class="divider-list">${(junction.earlier_tail || []).map(entry => `<div class="row"><div class="grow"><strong>${entry.number == null ? "blank" : "#" + entry.number}</strong><br><small>gate ${entry.observations[0].gate_index} · ${e(entry.observations[0].condition)}</small></div></div>`).join("") || "<p class='soft'>No pictures.</p>"}</div></div>
          <div><h4>Later scan · first pictures</h4><div class="divider-list">${(junction.later_head || []).map(entry => `<div class="row"><div class="grow"><strong>${entry.number == null ? "blank" : "#" + entry.number}</strong><br><small>gate ${entry.observations[0].gate_index} · ${e(entry.observations[0].condition)}</small></div></div>`).join("") || "<p class='soft'>No pictures.</p>"}</div></div>
        </div>
        <div class="actions">${btn("Join two views", "register-join", "secondary", "compare")}${btn("Record last repeated picture", "register-overlap", "primary", "check")}${btn("Scans share no picture", "register-adjacent", "secondary", "frame")}</div>`
      : `<p class="soft">${e(junction.note || "This overlap is settled.")}${anchor ? ` Last repeated picture: ${anchor.number == null ? "blank" : "#" + anchor.number}.` : ""}</p>`}
    </section>`;
  }).join("");
}

function joinedPanel(reg) {
  const joined = (reg.entries || []).filter(entry => entry.active && entry.observations.length > 1);
  if (!joined.length) return "";
  return `<section class="card stack"><h3>Pictures seen on more than one scan</h3>
    ${joined.map(entry => `<div class="row"><div class="grow"><strong>${entry.number == null ? "blank" : "#" + entry.number}</strong><br><small>${entry.observations.map(item => `${e(item.scan_name)} · gate ${item.gate_index}`).join(" + ")}</small><br><small>${e(entry.adoption_reason)}</small></div>
      ${btn("Open","register-entry","tiny secondary",null,`data-id="${entry.id}"`)}</div>`).join("")}
  </section>`;
}

function deliveryPanel(reg) {
  if (!reg.deliveries.length) return "";
  return `<section class="card stack"><h3>Delivered frame numbers</h3>
    ${reg.deliveries.slice().reverse().map(item => `<details open><summary>Frames ${item.from}–${item.through} · ${e(item.recipient)} · ${e(date(item.at))}${item.note ? " · " + e(item.note) : ""}</summary>
      ${table(["Frame","Adopted scan","Picture","Position"], item.frames.map(frame => [
        `#${frame.number}`, frame.photo_name, `gate ${frame.gate_index}`,
        frame.roi.map(value => n(value, 3)).join(", "),
      ]))}
      <div class="actions">${item.frames.filter(frame => frame.entry_id).map(frame => btn(`#${frame.number}`, "register-entry", "tiny ghost", null, `data-id="${frame.entry_id}"`)).join("")}</div></details>`).join("")}
    <p class="soft">Delivered numbers and their adopted scan positions are fixed. Later scans continue after them.</p>
  </section>`;
}

export function registerList(registers) {
  return `<header class="page-head"><div><h1>Frame registers</h1>
    <p>Carry one continuous frame numbering across two scans of the same reel and hand the delivered numbers to the timing desk.</p></div>
    ${btn("Start a register", "register-new", "primary", "plus")}</header>
    <div class="grid-detail"><section class="card stack"><h3>Registers</h3>
      ${registers.length ? `<div class="divider-list">${registers.map(item => `<div class="row"><div class="grow"><strong>${e(item.title)}</strong><br><small>${e(item.reel)} · ${item.scan_count} scans · ${item.frame_count} pictures · numbered through ${item.numbered_through}${item.sealed_through ? ` · delivered through ${item.sealed_through}` : ""}</small></div>
        ${btn("Open", "register-open", "tiny secondary", null, `data-id="${item.id}"`)}</div>`).join("")}</div>` : `<p class='soft'>No frame register yet. Start one from a Frame gate survey.</p>`}
    </section>
    <section class="card"><h3>How the table is written</h3>
      <p class="soft">A picture only gets a number when one or two scans show it clearly and its identity is settled. Across a splice, one scan is adopted for the repeated pictures, chosen by completeness; a clearer matching mark never changes it. Numbers after an unsettled picture stay blank.</p>
    </section></div>`;
}

export function registerPage(reg, entry) {
  const policy = reg.policy || {};
  return `<header class="page-head"><div><p class="eyebrow">Frame register</p><h1>${e(reg.title)}</h1>
      <p>${e(reg.reel)} · ${reg.segments.length} scans · ${reg.active_entries.length} pictures · numbered through ${reg.numbered_through} · delivered through ${reg.sealed_through}</p></div>
    <div class="actions">${btn("Back to registers", "register-list", "secondary", "back")}${btn("Attach scan", "register-attach", "primary", "plus")}${btn("Deliver frames", "register-deliver", "secondary", "check")}${link("Table CSV", `/api/registers/${reg.id}/table.csv`, "secondary", "download")}</div></header>
    <p class="notice">${e(policy.rule || "")}</p>
    <div class="grid-detail"><div class="stack">
      <section class="card stack"><div class="title-row"><h3>Picture table</h3>${badge(`${reg.blank_count} blank`, reg.blank_count ? "caution" : "ready")}</div>
        ${entries(reg)}
        <p class="soft">${reg.blank_count ? "A blank number means the picture's identity is not settled. Later numbers wait behind it." : "Every active picture has a number."}</p>
      </section>
      ${suspectPanel(reg)}
      ${junctionPanel(reg)}
      ${joinedPanel(reg)}
      ${deliveryPanel(reg)}
    </div><div class="stack">${entryPanel(reg, entry)}
      <section class="card stack"><h3>Scans in this register</h3>
        ${reg.segments.map(segment => `<div class="row"><div class="grow"><strong>${segment.order + 1}. ${e(segment.photo.name)}</strong><br><small>${e(segment.photo.role)} · ${segment.gate_count} pictures · ${segment.bounded_count} bounded · completeness ${n(segment.completeness, 3)} of a ${n(segment.nominal_width_px, 0)} px nominal picture</small></div><a class="button tiny secondary" href="${segment.overlay_url}" target="_blank" rel="noopener">Scan map</a></div>`).join("")}
      </section>
    </div></div>`;
}

export function attachForm(candidates, reel) {
  if (!candidates.length) return `<p class="soft">No unused Frame gate survey is available for ${e(reel)}. Run Frame gate survey on the next scan first.</p>`;
  return `<p class="soft">Attach the survey of the scan that carries the next stretch of the reel. The register keeps the earlier scan and its delivered numbers.</p>
    <div class="divider-list scroll-list">${candidates.map(item => `<label class="row field check"><input type="radio" name="result_id" value="${item.result_id}">
      <span class="grow"><strong>${e(item.photo_name)}</strong><br><small>${e(item.specimen_name)} · ${e(item.reel || "no reel")} · ${item.frames} pictures · ${e(date(item.created_at))}</small></span></label>`).join("")}</div>`;
}

export function joinForm(reg, junction, marks) {
  const tail = junction?.earlier_tail || [];
  const head = junction?.later_head || [];
  const chosen = marks || {};
  const earlier = entryById(reg, chosen.first) || tail[0];
  const later = entryById(reg, chosen.second) || head[0];
  const pairs = chosen.pairs || [];
  const crop = entry => {
    const observation = entry?.observations[0];
    return observation ? `/api/registers/${reg.id}/gates/${observation.segment_id}/${observation.gate_index}/crop.png` : "";
  };
  return `<div class="form-columns">
      <div><h4>Earlier scan</h4><div class="divider-list scroll-list">${tail.map(entry => `<label class="row field check"><input type="radio" name="first_id" value="${entry.id}"${entry.id === earlier?.id ? " checked" : ""}>
        <span class="grow"><strong>${entry.number == null ? "blank" : "#" + entry.number}</strong><br><small>gate ${entry.observations[0].gate_index} · ${e(entry.observations[0].condition)}</small></span></label>`).join("") || "<p class='soft'>No pictures.</p>"}</div></div>
      <div><h4>Later scan</h4><div class="divider-list scroll-list">${head.map(entry => `<label class="row field check"><input type="radio" name="second_id" value="${entry.id}"${entry.id === later?.id ? " checked" : ""}>
        <span class="grow"><strong>${entry.number == null ? "blank" : "#" + entry.number}</strong><br><small>gate ${entry.observations[0].gate_index} · ${e(entry.observations[0].condition)}</small></span></label>`).join("") || "<p class='soft'>No pictures.</p>"}</div></div>
    </div>
    <p class="soft">Point out at least two picture details that appear in both views. Click the same detail first in the earlier view, then in the later view.</p>
    <div class="stage-pair">
      <div><div class="stage"><div class="capture-drawing" data-mark="earlier">${img(crop(earlier), "Earlier view")}<svg viewBox="0 0 1000 1000" preserveAspectRatio="none" id="mark-earlier"></svg></div></div><p class="stage-foot">Earlier view</p></div>
      <div><div class="stage"><div class="capture-drawing" data-mark="later">${img(crop(later), "Later view")}<svg viewBox="0 0 1000 1000" preserveAspectRatio="none" id="mark-later"></svg></div></div><p class="stage-foot">Later view</p></div>
    </div>
    <div class="divider-list" id="join-marks">${pairs.map((pair, index) => `<div class="row"><div class="grow"><strong>Detail ${index + 1}</strong><br><small>earlier ${pair.first.map(value => n(value, 2)).join(", ")} · later ${pair.second.map(value => n(value, 2)).join(", ")}</small></div></div>`).join("") || "<p class='soft'>No matching detail marked yet. Click a detail in the earlier view, then the same detail in the later view.</p>"}</div>
    <fieldset class="field"><span>Which scan shows the matching marks more clearly?</span>
      <label class="field check"><input type="radio" name="clearer_marks" value="earlier"> Earlier scan</label>
      <label class="field check"><input type="radio" name="clearer_marks" value="later" checked> Later scan</label>
      <label class="field check"><input type="radio" name="clearer_marks" value="equal"> About equal</label>
      <small class="soft">This is recorded as evidence. It does not change which scan supplies the adopted image.</small>
    </fieldset>`;
}

export function overlapForm(reg, junction) {
  const pairs = reg.active_entries.filter(entry => {
    const ids = entry.observations.map(observation => observation.segment_id);
    return ids.includes(junction.earlier_segment_id) && ids.includes(junction.later_segment_id);
  });
  if (!pairs.length) {
    return `<p class="notice warn">No picture is joined across these two scans yet. Join the last picture both scans show first, or record the scans as sharing no picture.</p>`;
  }
  return `<p class="soft">Choose the last picture that both scans show. Every repeated picture before it must already be joined. Pictures after it belong to the later scan.</p>
    <div class="divider-list scroll-list">${pairs.map(entry => `<label class="row field check"><input type="radio" name="anchor_entry_id" value="${entry.id}">
      <span class="grow"><strong>${entry.number == null ? "blank" : "#" + entry.number}</strong><br><small>${entry.observations.map(item => `${e(item.scan_name)} · gate ${item.gate_index}`).join(" + ")}</small></span></label>`).join("")}</div>
    <p class="soft">The adopted scan for this splice is fixed once the overlap is closed.</p>`;
}

export function deliverForm(reg) {
  return `<p class="soft">Deliver the numbered pictures to the timing desk. Delivered numbers, and the scan and position each used, are kept in this register.</p>
    <div class="form-columns">
      <div class="field"><label for="field-through">Deliver through frame</label><input id="field-through" name="through" type="number" min="1" max="${reg.numbered_through}" value="${reg.numbered_through}" required></div>
      <div class="field"><label for="field-recipient">Recipient</label><input id="field-recipient" name="recipient" required maxlength="120" value="Timing desk"></div>
    </div>
    <div class="field"><label for="field-note">Delivery note</label><textarea id="field-note" name="note" maxlength="500"></textarea></div>
    <p class="notice">${reg.blank_count ? "Some pictures are still blank, so only frames through the last numbered picture can be delivered." : `Frames 1 through ${reg.numbered_through} can be delivered now.`}</p>`;
}

export function newRegisterForm(candidates, reels) {
  const reelOptions = reels.map(item => `<option value="${e(item.reel)}">${e(item.reel || "No reel")}</option>`).join("");
  return `<div class="form-columns">
      <div class="field"><label for="field-title">Register title</label><input id="field-title" name="title" maxlength="100" required></div>
      <div class="field"><label for="field-reel">Reel</label><select id="field-reel" name="reel">${reelOptions}</select></div>
    </div>
    <p class="soft">Start from the Frame gate survey of the first scan of this reel.</p>
    <div class="divider-list scroll-list">${candidates.map(item => `<label class="row field check"><input type="radio" name="result_id" value="${item.result_id}">
      <span class="grow"><strong>${e(item.photo_name)}</strong><br><small>${e(item.specimen_name)} · ${e(item.reel || "no reel")} · ${item.frames} pictures</small></span></label>`).join("") || "<p class='soft'>No Frame gate survey exists yet. Run one from a specimen first.</p>"}</div>`;
}
