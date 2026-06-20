import { Link, Route, Routes } from "react-router-dom";
import Inbox from "./pages/Inbox";
import Scene from "./pages/Scene";
import History from "./pages/History";
import Plan from "./pages/Plan";
import Manuscript from "./pages/Manuscript";

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">The Dominion Realm · Writers' Desk</Link>
        <nav>
          <Link to="/">Inbox</Link>
          <Link to="/plan">Plan</Link>
          <Link to="/history">History</Link>
          <Link to="/manuscript">Manuscript</Link>
        </nav>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<Inbox />} />
          <Route path="/plan" element={<Plan />} />
          <Route path="/scenes/:id" element={<Scene />} />
          <Route path="/history" element={<History />} />
          <Route path="/manuscript" element={<Manuscript />} />
        </Routes>
      </main>
    </div>
  );
}
