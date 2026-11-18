/**
 * "Reactivar conversación" con la plantilla de pedido listo: lleva la FOTO del
 * pedido en el encabezado. El operador la adjunta en el modal (se sube a Meta
 * como cualquier foto del chat) y el envío referencia ese `attachment_id`. Sin
 * foto subida no se puede enviar — Meta rechazaría la plantilla.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ReactivateConversationModal } from "./ReactivateConversationModal";

const sendTemplateMutate = vi.fn();
const uploadMock = vi.fn();
const compressMock = vi.fn();

const templatesFixture = [
  {
    name: "human_followup_utility_v1",
    category: "utility",
    semantics: "Seguimiento",
    body: "Hola, seguimiento a tu consulta {{1}}. Quedo atenta.",
    variables: [{ name: "followup_message", description: "Mensaje", max_length: 400 }],
    is_default: true,
    header_format: null,
  },
  {
    name: "order_ready_photo_utility_v1",
    category: "utility",
    semantics: "Pedido listo con foto",
    body: "Hola, tu pedido {{1}} ya está listo. Te compartimos la foto para que lo veas.",
    variables: [
      { name: "order_reference", description: "Número o referencia del pedido", max_length: 60 },
    ],
    is_default: false,
    header_format: "image",
  },
];

vi.mock("@plugins/chats/frontend/entities/handoff", async () => {
  const preview = await vi.importActual<
    typeof import("@plugins/chats/frontend/entities/handoff/model/templatePreview")
  >("@plugins/chats/frontend/entities/handoff/model/templatePreview");
  return {
    ...preview,
    useWhatsAppTemplates: () => ({ data: templatesFixture, isLoading: false, isError: false }),
    useSendTemplateMessageMutation: () => ({
      mutate: sendTemplateMutate,
      isPending: false,
      isError: false,
      error: null,
    }),
    uploadHumanMedia: (...args: unknown[]) => uploadMock(...args),
  };
});

vi.mock("@/shared/lib", async () => {
  const actual = await vi.importActual<typeof import("@/shared/lib")>("@/shared/lib");
  return {
    ...actual,
    compressImage: (file: File) => compressMock(file),
  };
});

function openPhotoTemplate() {
  render(<ReactivateConversationModal chatId="wa_573001234567" onClose={() => {}} />);
  fireEvent.change(screen.getByLabelText(/plantilla/i), {
    target: { value: "order_ready_photo_utility_v1" },
  });
  fireEvent.change(screen.getByLabelText(/número o referencia/i), {
    target: { value: "#31" },
  });
}

function pickPhoto() {
  const file = new File([new Uint8Array([0xff, 0xd8, 0xff])], "vela.jpg", {
    type: "image/jpeg",
  });
  fireEvent.change(screen.getByLabelText(/foto del pedido/i), { target: { files: [file] } });
  return file;
}

/** Clics que le llegan al selector de archivos (lo que abre el explorador). */
function watchPickerClicks() {
  const clicks = vi.fn();
  screen.getByLabelText(/foto del pedido/i).addEventListener("click", clicks);
  return clicks;
}

beforeEach(() => {
  sendTemplateMutate.mockReset();
  uploadMock.mockReset();
  compressMock.mockReset();
  compressMock.mockImplementation(async (file: File) => ({
    blob: file,
    mime: "image/jpeg",
    previewUrl: "blob:preview-pedido",
  }));
});

describe("ReactivateConversationModal · plantilla con foto", () => {
  it("text-only templates do not ask for a photo", () => {
    render(<ReactivateConversationModal chatId="wa_573001234567" onClose={() => {}} />);
    expect(screen.queryByLabelText(/foto del pedido/i)).not.toBeInTheDocument();
  });

  it("cannot send the photo template until the photo is attached", () => {
    openPhotoTemplate();

    expect(screen.getByLabelText(/foto del pedido/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /enviar plantilla/i })).toBeDisabled();
  });

  it("uploads the photo, previews it and sends it as the template header", async () => {
    uploadMock.mockResolvedValue({
      ok: true,
      attachment_id: "MEDIA_OK",
      media_ref: "/api/dashboard/media/wa_573001234567/out-abc.jpg",
    });
    openPhotoTemplate();
    pickPhoto();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /enviar plantilla/i })).toBeEnabled(),
    );
    expect(uploadMock).toHaveBeenCalledWith(
      "wa_573001234567",
      expect.any(File),
      expect.stringMatching(/\.jpg$/),
      expect.any(Function),
    );
    expect(screen.getByTestId("template-preview-photo")).toHaveAttribute(
      "src",
      "blob:preview-pedido",
    );

    fireEvent.click(screen.getByRole("button", { name: /enviar plantilla/i }));
    expect(sendTemplateMutate.mock.calls[0][0]).toEqual({
      template_name: "order_ready_photo_utility_v1",
      variables: { order_reference: "#31" },
      header_attachment_id: "MEDIA_OK",
      client_message_id: expect.any(String),
    });
  });

  it("shows a visible button to choose the photo, like the ready-order modal", () => {
    openPhotoTemplate();
    const clicks = watchPickerClicks();

    fireEvent.click(screen.getByText(/^elegir foto/i));

    expect(clicks).toHaveBeenCalled();
  });

  it("tapping the empty photo slot of the preview also opens the picker", () => {
    openPhotoTemplate();
    const clicks = watchPickerClicks();

    fireEvent.click(screen.getByText("{ foto del pedido }"));

    expect(clicks).toHaveBeenCalled();
  });

  it("once a photo is attached the button offers to change it", async () => {
    uploadMock.mockResolvedValue({ ok: true, attachment_id: "MEDIA_OK", media_ref: "/m.jpg" });
    openPhotoTemplate();
    pickPhoto();

    expect(await screen.findByText(/^cambiar foto/i)).toBeInTheDocument();
    expect(screen.queryByText(/^elegir foto/i)).not.toBeInTheDocument();
  });

  it("if the browser cannot shrink the photo it uploads the original file", async () => {
    compressMock.mockRejectedValue(new Error("no se pudo decodificar"));
    uploadMock.mockResolvedValue({ ok: true, attachment_id: "MEDIA_OK", media_ref: "/m.jpg" });
    openPhotoTemplate();
    const file = pickPhoto();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /enviar plantilla/i })).toBeEnabled(),
    );
    expect(uploadMock).toHaveBeenCalledWith(
      "wa_573001234567",
      file,
      expect.stringMatching(/\.jpg$/),
      expect.any(Function),
    );
  });

  it("previews the photo with a data: URL when it uploads the original file", async () => {
    compressMock.mockRejectedValue(new Error("no se pudo decodificar"));
    uploadMock.mockResolvedValue({ ok: true, attachment_id: "MEDIA_OK", media_ref: "/m.jpg" });
    openPhotoTemplate();
    pickPhoto();

    const img = await screen.findByTestId("template-preview-photo");
    expect(img.getAttribute("src")).toMatch(/^data:image\/jpeg;base64,/);
  });

  it("says the order number is missing when only the photo is attached", async () => {
    uploadMock.mockResolvedValue({ ok: true, attachment_id: "MEDIA_OK", media_ref: "/m.jpg" });
    render(<ReactivateConversationModal chatId="wa_573001234567" onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText(/plantilla/i), {
      target: { value: "order_ready_photo_utility_v1" },
    });
    pickPhoto();
    await screen.findByText(/foto lista/i);

    expect(screen.getByRole("button", { name: /enviar plantilla/i })).toBeDisabled();
    expect(screen.getByTestId("template-missing")).toHaveTextContent(
      /falta.*número o referencia del pedido/i,
    );

    fireEvent.change(screen.getByLabelText(/número o referencia/i), {
      target: { value: "#169" },
    });
    expect(screen.queryByTestId("template-missing")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /enviar plantilla/i })).toBeEnabled();
  });

  it("says the photo is missing when only the order number is written", () => {
    openPhotoTemplate();

    expect(screen.getByTestId("template-missing")).toHaveTextContent(/falta.*foto del pedido/i);
  });

  it("a failed upload shows the error and keeps sending blocked", async () => {
    uploadMock.mockRejectedValue(new Error("upload falló (HTTP 502)"));
    openPhotoTemplate();
    pickPhoto();

    expect(await screen.findByText(/upload falló \(HTTP 502\)/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /enviar plantilla/i })).toBeDisabled();
  });

  it("switching to a text-only template drops the photo from the send", async () => {
    uploadMock.mockResolvedValue({ ok: true, attachment_id: "MEDIA_OK", media_ref: "/m.jpg" });
    openPhotoTemplate();
    pickPhoto();
    await waitFor(() => expect(screen.getByTestId("template-preview-photo")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/plantilla/i), {
      target: { value: "human_followup_utility_v1" },
    });
    fireEvent.change(screen.getByLabelText(/mensaje/i), { target: { value: "hola" } });
    fireEvent.click(screen.getByRole("button", { name: /enviar plantilla/i }));

    expect(sendTemplateMutate.mock.calls[0][0]).not.toHaveProperty("header_attachment_id");
    expect(screen.queryByTestId("template-preview-photo")).not.toBeInTheDocument();
  });
});
