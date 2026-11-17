import { Database, Globe, Layers, Server, Sprout, Target } from "lucide-react";
import useDocumentTitle from "../../hooks/useDocumentTitle";
import { GUIDES, ORG_NAME, ORG_SHORT, TEAM } from "../../config/constants";

function Block({ icon: Icon, title, children }) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <h3 className="mb-3 flex items-center gap-2 text-lg font-bold text-slate-900"><Icon className="h-5 w-5 text-emerald-600" /> {title}</h3>
      <div className="space-y-3 text-sm leading-relaxed text-slate-600">{children}</div>
    </section>
  );
}

function Person({ p }) {
  return (
    <li className="rounded-xl border border-slate-200 bg-slate-50 p-4">
      <p className="font-semibold text-slate-900">{p.name}</p>
      <p className="text-xs font-medium text-emerald-700">{p.role}</p>
      <p className="mt-0.5 text-xs text-slate-500">{p.org}</p>
    </li>
  );
}

export default function About() {
  useDocumentTitle("About Us");
  return (
    <div className="mx-auto max-w-4xl space-y-6 px-4 py-10 sm:px-8">
      <header>
        <h2 className="text-3xl font-bold tracking-tight text-slate-900">About Us</h2>
        <p className="mt-2 text-sm text-slate-600">The institute, the project, and the people behind the Agricultural Well Detection and Analysis System.</p>
      </header>

      <Block icon={Sprout} title="About ICAR">
        <p>The Indian Council of Agricultural Research (ICAR) is the apex autonomous body for coordinating agricultural research and education in India, under the Department of Agricultural Research and Education, Ministry of Agriculture and Farmers Welfare, Government of India. Its headquarters are in New Delhi.</p>
      </Block>
      <Block icon={Globe} title={`About ${ORG_SHORT}`}>
        <p>{ORG_NAME} ({ORG_SHORT}), Nagpur, works on soil survey, land resource assessment, land use planning, and the use of remote sensing and GIS in agricultural resource management.</p>
      </Block>
      <Block icon={Target} title="Introduction">
        <p>Open wells are a main source of irrigation water in semi-arid India, yet up-to-date, georeferenced well records are rare because they depend on slow field surveys. This system finds wells automatically in satellite imagery, so that agencies can map them quickly for groundwater assessment, cropping-system planning and land use planning.</p>
      </Block>
    </div>
  );
}
