// Self-submit complete-set ops FROM the Gnosis Safe (std0's wallet-type, zero relayer / zero quota).
// Funds live in the Safe (the CLOB funder, sig_type 2); the owner EOA signs Safe.execTransaction and
// pays gas in POL. This is the second pillar after "CLOB accepts the Safe as maker".
//   node safeops.js approve                 ensure Safe->adapter pUSD allowance + CTF setApprovalForAll
//   node safeops.js mint   <cid> <usd>      pUSD -> Up+Down complete sets, HELD BY THE SAFE
//   node safeops.js merge  <cid> <shares>   Up+Down -> pUSD (instant breakeven recovery)
//   node safeops.js redeem <cid>            winning side of a settled market -> pUSD
//   node safeops.js bal    <cid>            print Safe pUSD + Up/Down token balances
// env: POLY_PRIVATE_KEY (owner 0xE8dc), POLY_FUNDER (the Safe 0xBbe3).
const { ethers } = require("ethers");
const ADAPTER = "0xAdA100Db00Ca00073811820692005400218FcE1f";
const PUSD = "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb";
const CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045";
const RPC = "https://polygon-bor-rpc.publicnode.com";

const adI = new ethers.utils.Interface([
  "function splitPosition(address,bytes32,bytes32,uint256[],uint256)",
  "function mergePositions(address,bytes32,bytes32,uint256[],uint256)",
  "function redeemPositions(address,bytes32,bytes32,uint256[])",
]);
const erc20I = new ethers.utils.Interface(["function approve(address,uint256)"]);
const ctfI = new ethers.utils.Interface(["function setApprovalForAll(address,bool)"]);
const SAFE_ABI = [
  "function nonce() view returns (uint256)",
  "function getOwners() view returns (address[])",
  "function getTransactionHash(address to,uint256 value,bytes data,uint8 operation,uint256 safeTxGas,uint256 baseGas,uint256 gasPrice,address gasToken,address refundReceiver,uint256 _nonce) view returns (bytes32)",
  "function execTransaction(address to,uint256 value,bytes data,uint8 operation,uint256 safeTxGas,uint256 baseGas,uint256 gasPrice,address gasToken,address refundReceiver,bytes signatures) payable returns (bool)",
];

(async () => {
  const [cmd, cid, arg] = process.argv.slice(2);
  const prov = new ethers.providers.JsonRpcProvider(RPC);

  if (cmd === "baltoks") {   // READ-ONLY (NO private key): pUSD + held tokens by explicit token ids — the bot's on-chain inventory sync
    const f = (x) => +ethers.utils.formatUnits(x, 6);
    const SAFE = ethers.utils.getAddress(process.env.POLY_FUNDER);
    const pusd = new ethers.Contract(PUSD, ["function balanceOf(address) view returns(uint256)"], prov);
    const ctf = new ethers.Contract(CTF, ["function balanceOf(address,uint256) view returns(uint256)"], prov);
    console.log("Safe pUSD:", f(await pusd.balanceOf(SAFE)));
    console.log("Up:", f(await ctf.balanceOf(SAFE, cid)), "Down:", f(await ctf.balanceOf(SAFE, arg)));
    return;
  }

  const owner = new ethers.Wallet(process.env.POLY_PRIVATE_KEY, prov);
  const SAFE = ethers.utils.getAddress(process.env.POLY_FUNDER);
  const safe = new ethers.Contract(SAFE, SAFE_ABI, owner);
  const Z = ethers.constants.HashZero;
  const fmt = (x) => +ethers.utils.formatUnits(x, 6);

  const owners = await safe.getOwners();
  if (!owners.map((a) => a.toLowerCase()).includes(owner.address.toLowerCase())) {
    console.error("ERR key", owner.address, "is NOT an owner of Safe", SAFE, "owners=", owners); process.exit(2);
  }

  // read-only helper
  const pusd = new ethers.Contract(PUSD, ["function balanceOf(address) view returns(uint256)", "function allowance(address,address) view returns(uint256)"], prov);
  const ctf = new ethers.Contract(CTF, ["function balanceOf(address,uint256) view returns(uint256)", "function isApprovedForAll(address,address) view returns(bool)"], prov);

  const mk = cid ? await (async () => {
    const https = require("https");
    const j = await new Promise((res, rej) => https.get(`https://gamma-api.polymarket.com/markets?condition_ids=${cid}`, (r) => { let d = ""; r.on("data", c => d += c); r.on("end", () => res(JSON.parse(d))); }).on("error", rej));
    return Array.isArray(j) ? j[0] : j;
  })() : null;
  const toks = mk && mk.clobTokenIds ? JSON.parse(mk.clobTokenIds) : null;

  if (cmd === "bal") {
    console.log("Safe pUSD:", fmt(await pusd.balanceOf(SAFE)));
    if (toks) console.log("Up:", fmt(await ctf.balanceOf(SAFE, toks[0])), "Down:", fmt(await ctf.balanceOf(SAFE, toks[1])));
    return;
  }

  // --- build the inner call ---
  const inner = (() => {
    if (cmd === "approve") return null; // handled below as two txs
    if (cmd === "mint") return { to: ADAPTER, data: adI.encodeFunctionData("splitPosition", [PUSD, Z, cid, [1, 2], ethers.utils.parseUnits(arg, 6)]) };
    if (cmd === "merge") return { to: ADAPTER, data: adI.encodeFunctionData("mergePositions", [PUSD, Z, cid, [1, 2], ethers.utils.parseUnits(arg, 6)]) };
    if (cmd === "redeem") return { to: ADAPTER, data: adI.encodeFunctionData("redeemPositions", [PUSD, Z, cid, [1, 2]]) };
    console.error("ERR unknown cmd", cmd); process.exit(2);
  })();

  const gp = await prov.getGasPrice();
  const txOpts = { maxFeePerGas: gp.mul(2), maxPriorityFeePerGas: ethers.utils.parseUnits("50", "gwei"), gasLimit: 700000 };

  // execute one Safe tx (to, data) self-submitted, owner signs the EIP-712 SafeTx digest
  async function execSafe(to, data, label) {
    const nonce = await safe.nonce();
    const safeTxHash = await safe.getTransactionHash(to, 0, data, 0, 0, 0, 0, ethers.constants.AddressZero, ethers.constants.AddressZero, nonce);
    const sig = owner._signingKey().signDigest(safeTxHash);       // ECDSA over the typed-data digest -> v 27/28
    const signatures = ethers.utils.joinSignature(sig);
    const tx = await safe.execTransaction(to, 0, data, 0, 0, 0, 0, ethers.constants.AddressZero, ethers.constants.AddressZero, signatures, txOpts);
    const r = await tx.wait();
    console.log((r.status === 1 ? "OK " : "FAIL ") + label, "status", r.status, "tx", tx.hash);
    if (r.status !== 1) throw new Error(label + " reverted");
  }

  // approvals (idempotent): Safe must approve adapter to pull pUSD (split) + setApprovalForAll on CTF (merge/redeem burn the Safe's tokens)
  if (cmd === "approve" || cmd === "mint" || cmd === "merge" || cmd === "redeem") {
    if ((await pusd.allowance(SAFE, ADAPTER)).lt(ethers.utils.parseUnits("100000", 6))) {
      await execSafe(PUSD, erc20I.encodeFunctionData("approve", [ADAPTER, ethers.constants.MaxUint256]), "approve(pUSD->adapter)");
    } else console.log("pUSD allowance already set");
    if (!(await ctf.isApprovedForAll(SAFE, ADAPTER))) {
      await execSafe(CTF, ctfI.encodeFunctionData("setApprovalForAll", [ADAPTER, true]), "setApprovalForAll(CTF->adapter)");
    } else console.log("CTF approval already set");
  }
  if (cmd === "approve") { console.log(">>> approvals OK"); return; }

  await execSafe(inner.to, inner.data, cmd);
  await new Promise((r) => setTimeout(r, 4000));
  console.log("--- post-op balances ---");
  console.log("Safe pUSD:", fmt(await pusd.balanceOf(SAFE)));
  if (toks) console.log("Up:", fmt(await ctf.balanceOf(SAFE, toks[0])), "Down:", fmt(await ctf.balanceOf(SAFE, toks[1])));
})().catch((e) => { console.error("ERR", (e && (e.message || JSON.stringify(e))) ? (e.message || JSON.stringify(e)).slice(0, 300) : e); process.exit(1); });
